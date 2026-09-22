import asyncio
import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin

import httpx

from bukmatika.acquisition.network import PinnedTarget, resolve_public_https


class RemoteDownloadError(RuntimeError):
    pass


class DownloadTooLarge(RemoteDownloadError):
    pass


class DownloadCancelled(RemoteDownloadError):
    pass


class DownloadInterrupted(RemoteDownloadError):
    pass


class _Digest(Protocol):
    def update(self, data: bytes, /) -> None: ...

    def hexdigest(self) -> str: ...


@dataclass(frozen=True, slots=True)
class DownloadResult:
    temp_path: Path
    sha256: str
    byte_size: int
    media_type: str | None
    final_url: str
    redirect_count: int


TargetResolver = Callable[[str], Awaitable[PinnedTarget]]
CancellationProbe = Callable[[], Awaitable[bool]]

_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")


class SafeDownloader:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_bytes: int,
        redirect_limit: int,
        chunk_size: int,
        timeout_seconds: float,
        user_agent: str,
        resume_limit: int = 2,
        resolver: TargetResolver = resolve_public_https,
    ) -> None:
        if resume_limit < 0:
            raise ValueError("resume_limit must be nonnegative")
        self._client = client
        self._max_bytes = max_bytes
        self._redirect_limit = redirect_limit
        self._chunk_size = chunk_size
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._resume_limit = resume_limit
        self._resolver = resolver
        self._cancel_check_bytes = max(chunk_size, 1_048_576)

    async def download(
        self,
        url: str,
        temp_path: Path,
        *,
        cancellation_probe: CancellationProbe | None = None,
    ) -> DownloadResult:
        current_url = url
        redirects = 0
        resume_from = 0
        resume_validator: str | None = None
        resume_attempts = 0

        while True:
            await _raise_if_cancelled(cancellation_probe)
            target = await self._resolver(current_url)
            headers = {
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Host": target.host_header,
                "User-Agent": self._user_agent,
            }
            if resume_from > 0:
                if resume_validator is None:
                    raise RuntimeError("Resume offset exists without a validator")
                headers["Range"] = f"bytes={resume_from}-"
                headers["If-Range"] = resume_validator

            async with self._client.stream(
                "GET",
                target.request_url,
                headers=headers,
                extensions={"sni_hostname": target.sni_hostname},
                timeout=self._timeout_seconds,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    if resume_from > 0:
                        raise RemoteDownloadError("Resumed acquisition redirected")
                    location = response.headers.get("location")
                    if location is None:
                        raise RemoteDownloadError("Redirect response did not include Location")
                    if redirects >= self._redirect_limit:
                        raise RemoteDownloadError("Acquisition redirect limit exceeded")
                    current_url = urljoin(target.original_url, location)
                    redirects += 1
                    continue

                append = False
                initial_total = 0
                expected_total: int | None = None
                validator: str | None
                resume_supported: bool

                if resume_from > 0:
                    if response.status_code == 206:
                        range_start, range_end, range_total = _content_range(
                            response.headers.get("content-range")
                        )
                        if range_start != resume_from:
                            raise RemoteDownloadError(
                                "Resume response did not start at the requested byte offset"
                            )
                        if range_end < range_start:
                            raise RemoteDownloadError(
                                "Resume response returned an invalid byte range"
                            )
                        if range_total is not None:
                            if range_total > self._max_bytes:
                                raise DownloadTooLarge(
                                    "Acquisition source exceeds configured byte limit"
                                )
                            expected_total = range_total
                        remaining_length = _content_length(
                            response.headers.get("content-length")
                        )
                        if remaining_length is not None:
                            if remaining_length != range_end - range_start + 1:
                                raise RemoteDownloadError(
                                    "Resume response Content-Length does not match Content-Range"
                                )
                            if resume_from + remaining_length > self._max_bytes:
                                raise DownloadTooLarge(
                                    "Acquisition source exceeds configured byte limit"
                                )
                            if expected_total is None:
                                expected_total = resume_from + remaining_length
                        append = True
                        initial_total = resume_from
                        validator = resume_validator
                        resume_supported = True
                    elif response.status_code == 200:
                        # If-Range may legally produce a full response when the remote object
                        # changed. Restart from byte zero instead of stitching different bodies.
                        content_length = _content_length(
                            response.headers.get("content-length")
                        )
                        if content_length is not None and content_length > self._max_bytes:
                            raise DownloadTooLarge(
                                "Acquisition source exceeds configured byte limit"
                            )
                        expected_total = content_length
                        validator = _resume_validator(response.headers)
                        resume_supported = (
                            _accepts_byte_ranges(response.headers) and validator is not None
                        )
                        resume_from = 0
                        resume_validator = None
                    else:
                        raise RemoteDownloadError(
                            f"Resume source returned HTTP {response.status_code}"
                        )
                else:
                    if response.status_code != 200:
                        raise RemoteDownloadError(
                            f"Acquisition source returned HTTP {response.status_code}"
                        )
                    content_length = _content_length(response.headers.get("content-length"))
                    if content_length is not None and content_length > self._max_bytes:
                        raise DownloadTooLarge(
                            "Acquisition source exceeds configured byte limit"
                        )
                    expected_total = content_length
                    validator = _resume_validator(response.headers)
                    resume_supported = (
                        _accepts_byte_ranges(response.headers) and validator is not None
                    )

                media_type = _media_type(response.headers.get("content-type"))
                try:
                    return await self._stream_body(
                        response,
                        temp_path=temp_path,
                        media_type=media_type,
                        final_url=target.original_url,
                        redirects=redirects,
                        cancellation_probe=cancellation_probe,
                        append=append,
                        initial_total=initial_total,
                        expected_total=expected_total,
                    )
                except (httpx.TransportError, DownloadInterrupted):
                    partial_size = await asyncio.to_thread(_file_size, temp_path)
                    if (
                        not resume_supported
                        or validator is None
                        or partial_size <= 0
                        or partial_size >= self._max_bytes
                        or resume_attempts >= self._resume_limit
                    ):
                        raise
                    resume_from = partial_size
                    resume_validator = validator
                    resume_attempts += 1
                    current_url = target.original_url

    async def _stream_body(
        self,
        response: httpx.Response,
        *,
        temp_path: Path,
        media_type: str | None,
        final_url: str,
        redirects: int,
        cancellation_probe: CancellationProbe | None,
        append: bool,
        initial_total: int,
        expected_total: int | None,
    ) -> DownloadResult:
        digest: _Digest = (
            await asyncio.to_thread(_sha256_file, temp_path)
            if append
            else hashlib.sha256()
        )
        total = initial_total
        next_cancel_check = max(total + self._cancel_check_bytes, self._cancel_check_bytes)
        handle = temp_path.open("ab" if append else "wb")
        try:
            async for chunk in response.aiter_bytes(chunk_size=self._chunk_size):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self._max_bytes:
                    raise DownloadTooLarge("Acquisition stream exceeded configured byte limit")
                if expected_total is not None and total > expected_total:
                    raise RemoteDownloadError(
                        "Acquisition source exceeded its declared response length"
                    )
                if total >= next_cancel_check:
                    await _raise_if_cancelled(cancellation_probe)
                    next_cancel_check = total + self._cancel_check_bytes
                digest.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
            await _raise_if_cancelled(cancellation_probe)
            await asyncio.to_thread(handle.flush)
        finally:
            await asyncio.to_thread(handle.close)

        if total == 0:
            raise RemoteDownloadError("Acquisition source returned an empty body")
        if expected_total is not None and total != expected_total:
            raise DownloadInterrupted(
                "Acquisition source ended before the declared response length"
            )
        return DownloadResult(
            temp_path=temp_path,
            sha256=digest.hexdigest(),
            byte_size=total,
            media_type=media_type,
            final_url=final_url,
            redirect_count=redirects,
        )


async def _raise_if_cancelled(probe: CancellationProbe | None) -> None:
    if probe is not None and await probe():
        raise DownloadCancelled("Acquisition was cancelled")


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RemoteDownloadError("Acquisition source returned invalid Content-Length") from exc
    if parsed < 0:
        raise RemoteDownloadError("Acquisition source returned negative Content-Length")
    return parsed


def _content_range(value: str | None) -> tuple[int, int, int | None]:
    if value is None:
        raise RemoteDownloadError("Resume response did not include Content-Range")
    match = _CONTENT_RANGE.fullmatch(value.strip())
    if match is None:
        raise RemoteDownloadError("Resume response returned invalid Content-Range")
    start = int(match.group(1))
    end = int(match.group(2))
    total_text = match.group(3)
    total = None if total_text == "*" else int(total_text)
    if total is not None and (total <= end or total <= start):
        raise RemoteDownloadError("Resume response returned inconsistent Content-Range")
    return start, end, total


def _resume_validator(headers: httpx.Headers) -> str | None:
    etag = headers.get("etag")
    if etag is not None and not etag.lstrip().startswith("W/"):
        return etag
    last_modified = headers.get("last-modified")
    return last_modified or None


def _accepts_byte_ranges(headers: httpx.Headers) -> bool:
    return headers.get("accept-ranges", "").strip().lower() == "bytes"


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _sha256_file(path: Path) -> _Digest:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type or None
