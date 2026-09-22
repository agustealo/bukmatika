from pathlib import Path

import httpx
import pytest

from bukmatika.acquisition.downloader import (
    DownloadTooLarge,
    RemoteDownloadError,
    SafeDownloader,
)
from bukmatika.acquisition.network import PinnedTarget


class _InterruptingStream(httpx.AsyncByteStream):
    def __init__(self, partial: bytes) -> None:
        self._partial = partial

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self._partial
        raise httpx.ReadError("simulated connection loss")


def _target(original_url: str, ip: str) -> PinnedTarget:
    host = original_url.split("/", 3)[2]
    suffix = "/" + original_url.split("/", 3)[3] if original_url.count("/") >= 3 else "/"
    return PinnedTarget(
        original_url=original_url,
        request_url=f"https://{ip}{suffix}",
        host_header=host,
        sni_hostname=host,
        resolved_ip=ip,
    )


async def test_redirect_target_is_revalidated_and_pinned(tmp_path: Path) -> None:
    resolved: list[str] = []

    async def resolver(url: str) -> PinnedTarget:
        resolved.append(url)
        if "first.example" in url:
            return _target(url, "93.184.216.34")
        return _target(url, "1.1.1.1")

    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "93.184.216.34":
            return httpx.Response(
                302,
                headers={"Location": "https://second.example/book.pdf"},
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nverified",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = SafeDownloader(
            client,
            max_bytes=1024,
            redirect_limit=3,
            chunk_size=64,
            timeout_seconds=5,
            user_agent="Bukmatika-Test",
            resolver=resolver,
        )
        result = await downloader.download(
            "https://first.example/book.pdf",
            tmp_path / "download.part",
        )

    assert resolved == [
        "https://first.example/book.pdf",
        "https://second.example/book.pdf",
    ]
    assert [request.url.host for request in seen] == ["93.184.216.34", "1.1.1.1"]
    assert seen[0].headers["host"] == "first.example"
    assert seen[1].headers["host"] == "second.example"
    assert result.redirect_count == 1
    assert result.byte_size == len(b"%PDF-1.7\nverified")
    assert result.temp_path.read_bytes() == b"%PDF-1.7\nverified"


async def test_declared_oversized_response_is_rejected_before_body_write(tmp_path: Path) -> None:
    async def resolver(url: str) -> PinnedTarget:
        return _target(url, "93.184.216.34")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": "4096",
            },
            content=b"%PDF-1.7\nsmall",
            request=request,
        )

    destination = tmp_path / "oversized.part"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = SafeDownloader(
            client,
            max_bytes=128,
            redirect_limit=0,
            chunk_size=64,
            timeout_seconds=5,
            user_agent="Bukmatika-Test",
            resolver=resolver,
        )
        with pytest.raises(DownloadTooLarge):
            await downloader.download("https://books.example/book.pdf", destination)

    assert not destination.exists()


async def test_interrupted_download_resumes_with_range_and_if_range(tmp_path: Path) -> None:
    full_body = b"%PDF-1.7\nresumable-body"
    partial = full_body[:8]
    seen: list[httpx.Request] = []

    async def resolver(url: str) -> PinnedTarget:
        return _target(url, "93.184.216.34")

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                200,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(full_body)),
                    "Content-Type": "application/pdf",
                    "ETag": '"book-v1"',
                },
                stream=_InterruptingStream(partial),
                request=request,
            )
        remainder = full_body[len(partial) :]
        assert request.headers["range"] == f"bytes={len(partial)}-"
        assert request.headers["if-range"] == '"book-v1"'
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(len(remainder)),
                "Content-Range": f"bytes {len(partial)}-{len(full_body) - 1}/{len(full_body)}",
                "Content-Type": "application/pdf",
                "ETag": '"book-v1"',
            },
            content=remainder,
            request=request,
        )

    destination = tmp_path / "resume.part"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = SafeDownloader(
            client,
            max_bytes=1024,
            redirect_limit=0,
            chunk_size=8,
            timeout_seconds=5,
            user_agent="Bukmatika-Test",
            resolver=resolver,
        )
        result = await downloader.download("https://books.example/book.pdf", destination)

    assert len(seen) == 2
    assert destination.read_bytes() == full_body
    assert result.byte_size == len(full_body)


async def test_if_range_full_response_restarts_instead_of_stitching(tmp_path: Path) -> None:
    original = b"%PDF-1.7\nold-version"
    partial = original[:8]
    replacement = b"%PDF-1.7\nnew-version"
    seen: list[httpx.Request] = []

    async def resolver(url: str) -> PinnedTarget:
        return _target(url, "93.184.216.34")

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                200,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(original)),
                    "Content-Type": "application/pdf",
                    "ETag": '"book-v1"',
                },
                stream=_InterruptingStream(partial),
                request=request,
            )
        assert request.headers["range"] == f"bytes={len(partial)}-"
        assert request.headers["if-range"] == '"book-v1"'
        return httpx.Response(
            200,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(replacement)),
                "Content-Type": "application/pdf",
                "ETag": '"book-v2"',
            },
            content=replacement,
            request=request,
        )

    destination = tmp_path / "changed.part"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = SafeDownloader(
            client,
            max_bytes=1024,
            redirect_limit=0,
            chunk_size=8,
            timeout_seconds=5,
            user_agent="Bukmatika-Test",
            resolver=resolver,
        )
        result = await downloader.download("https://books.example/book.pdf", destination)

    assert len(seen) == 2
    assert destination.read_bytes() == replacement
    assert result.byte_size == len(replacement)


async def test_resume_rejects_misaligned_content_range(tmp_path: Path) -> None:
    full_body = b"%PDF-1.7\nresumable-body"
    partial = full_body[:8]
    seen: list[httpx.Request] = []

    async def resolver(url: str) -> PinnedTarget:
        return _target(url, "93.184.216.34")

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                200,
                headers={
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(len(full_body)),
                    "Content-Type": "application/pdf",
                    "ETag": '"book-v1"',
                },
                stream=_InterruptingStream(partial),
                request=request,
            )
        remainder = full_body[len(partial) :]
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(len(remainder)),
                "Content-Range": (
                    f"bytes {len(partial) + 1}-{len(full_body) - 1}/{len(full_body)}"
                ),
                "Content-Type": "application/pdf",
            },
            content=remainder,
            request=request,
        )

    destination = tmp_path / "misaligned.part"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        downloader = SafeDownloader(
            client,
            max_bytes=1024,
            redirect_limit=0,
            chunk_size=8,
            timeout_seconds=5,
            user_agent="Bukmatika-Test",
            resolver=resolver,
        )
        with pytest.raises(RemoteDownloadError, match="requested byte offset"):
            await downloader.download("https://books.example/book.pdf", destination)

    assert len(seen) == 2
