import httpx
import pytest
from starlette.types import ASGIApp, Receive, Scope, Send

from bukmatika.http_security import BrowserOriginWriteMiddleware

_ALLOWED_ORIGIN = "http://127.0.0.1:3000"


class _RecordingApp:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _client(app: ASGIApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1:8000",
    )


@pytest.mark.asyncio
async def test_browser_write_accepts_exact_canonical_origin() -> None:
    downstream = _RecordingApp()
    app = BrowserOriginWriteMiddleware(downstream, allowed_origin=_ALLOWED_ORIGIN)
    async with _client(app) as client:
        response = await client.post("/v1/write", headers={"Origin": _ALLOWED_ORIGIN})

    assert response.status_code == 204
    assert downstream.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:4000",
        "http://localhost:3000",
        "https://attacker.example",
        "null",
    ],
)
async def test_browser_write_rejects_noncanonical_origin(origin: str) -> None:
    downstream = _RecordingApp()
    app = BrowserOriginWriteMiddleware(downstream, allowed_origin=_ALLOWED_ORIGIN)
    async with _client(app) as client:
        response = await client.post("/v1/write", headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "ORIGIN_NOT_ALLOWED"}}
    assert response.headers["cache-control"] == "no-store"
    assert downstream.calls == 0


@pytest.mark.asyncio
async def test_browser_write_rejects_duplicate_origin_headers() -> None:
    downstream = _RecordingApp()
    app = BrowserOriginWriteMiddleware(downstream, allowed_origin=_ALLOWED_ORIGIN)
    async with _client(app) as client:
        response = await client.post(
            "/v1/write",
            headers=[("Origin", _ALLOWED_ORIGIN), ("Origin", _ALLOWED_ORIGIN)],
        )

    assert response.status_code == 403
    assert downstream.calls == 0


@pytest.mark.asyncio
async def test_nonbrowser_write_without_origin_remains_supported() -> None:
    downstream = _RecordingApp()
    app = BrowserOriginWriteMiddleware(downstream, allowed_origin=_ALLOWED_ORIGIN)
    async with _client(app) as client:
        response = await client.post("/v1/write")

    assert response.status_code == 204
    assert downstream.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_methods_are_not_origin_fenced(method: str) -> None:
    downstream = _RecordingApp()
    app = BrowserOriginWriteMiddleware(downstream, allowed_origin=_ALLOWED_ORIGIN)
    async with _client(app) as client:
        response = await client.request(
            method,
            "/v1/read",
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 204
    assert downstream.calls == 1
