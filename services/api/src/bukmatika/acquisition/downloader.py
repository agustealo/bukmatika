import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx

from bukmatika.acquisition.network import PinnedTarget, resolve_public_https


class RemoteDownloadError(RuntimeError):
    pass


class DownloadTooLarge(RemoteDownloadError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadResult:
    temp_path: Path
    sha256: str
    byte_size: int
    media_type: str | None
    final_url: str
    redirect_count: int


TargetResolver = Callable[[str], Awaitable[PinnedTarget]]


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
        resolver: TargetResolver = resolve_public_https,
    ) -> None:
        self._client = client
        self._max_bytes = max_bytes
        self._redirect_limit = redirect_limit
        self._chunk_size = chunk_size
        self._timeout_seconds = timeout_seconds
        self._user_agent = user_agent
        self._resolver = resolver

    async def download(self, url: str, temp_path: Path) -> DownloadResult:
        current_url = url
        redirects = 0
        while True:
            target = await self._resolver(current_url)
            headers = {
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "Host": target.host_header,
                "User-Agent": self._user_agent,
            }
            async with self._client.stream(
                "GET",
                target.request_url,
                headers=headers,
                extensions={"sni_hostname": target.sni_hostname},
                timeout=self._timeout_seconds,
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if location is None:
                        raise RemoteDownloadError("Redirect response did not include Location")
                    if redirects >= self._redirect_limit:
                        raise RemoteDownloadError("Acquisition redirect limit exceeded")
                    current_url = urljoin(target.original_url, location)
                    redirects += 1
                    continue

                if response.status_code != 200:
                    raise RemoteDownloadError(
                        f"Acquisition source returned HTTP {response.status_code}"
                    )

                content_length = _content_length(response.headers.get("content-length"))
                if content_length is not None and content_length > self._max_bytes:
                    raise DownloadTooLarge("Acquisition source exceeds configured byte limit")

                media_type = _media_type(response.headers.get("content-type"))
                return await self._stream_body(
                    response,
                    temp_path=temp_path,
                    media_type=media_type,
                    final_url=target.original_url,
                    redirects=redirects,
                )

    async def _stream_body(
        self,
        response: httpx.Response,
        *,
        temp_path: Path,
        media_type: str | None,
        final_url: str,
        redirects: int,
    ) -> DownloadResult:
        digest = hashlib.sha256()
        total = 0
        handle = temp_path.open("wb")
        try:
            async for chunk in response.aiter_bytes(chunk_size=self._chunk_size):
                if not chunk:
                    continue
                total += len(chunk)
                if total > self._max_bytes:
                    raise DownloadTooLarge("Acquisition stream exceeded configured byte limit")
                digest.update(chunk)
                await asyncio.to_thread(handle.write, chunk)
            await asyncio.to_thread(handle.flush)
        finally:
            await asyncio.to_thread(handle.close)

        if total == 0:
            raise RemoteDownloadError("Acquisition source returned an empty body")
        return DownloadResult(
            temp_path=temp_path,
            sha256=digest.hexdigest(),
            byte_size=total,
            media_type=media_type,
            final_url=final_url,
            redirect_count=redirects,
        )


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


def _media_type(value: str | None) -> str | None:
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type or None
