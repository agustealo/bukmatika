from pathlib import Path

import httpx
import pytest

from bukmatika.acquisition.downloader import DownloadTooLarge, SafeDownloader
from bukmatika.acquisition.network import PinnedTarget


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
