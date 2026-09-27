import httpx
import pytest
from starlette.types import ASGIApp, Receive, Scope, Send

from bukmatika.http_security import BrowserOriginWriteMiddleware, PrivateResponseCacheMiddleware

_ALLOWED_ORIGIN = "http://127.0.0.1:3000"


class _RecordingApp:
    def __init__(self, *, headers: list[tuple[bytes, bytes]] | None = None) -> None:
        self.calls = 0
        self._headers = headers or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": list(self._headers),
            }
        )
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


@pytest.mark.asyncio
async def test_cookie_bound_response_is_private_and_not_stored() -> None:
    downstream = _RecordingApp(headers=[(b"cache-control", b"public, max-age=600")])
    app = PrivateResponseCacheMiddleware(downstream)
    async with _client(app) as client:
        response = await client.get("/v1/private", headers={"Cookie": "bukmatika_session=secret"})

    assert response.status_code == 204
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert downstream.calls == 1


@pytest.mark.asyncio
async def test_session_bootstrap_response_that_sets_cookie_is_not_stored() -> None:
    downstream = _RecordingApp(
        headers=[(b"set-cookie", b"bukmatika_session=secret; HttpOnly; Path=/")]
    )
    app = PrivateResponseCacheMiddleware(downstream)
    async with _client(app) as client:
        response = await client.post("/v1/session/local")

    assert response.status_code == 204
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_anonymous_response_keeps_endpoint_cache_policy() -> None:
    downstream = _RecordingApp(headers=[(b"cache-control", b"public, max-age=600")])
    app = PrivateResponseCacheMiddleware(downstream)
    async with _client(app) as client:
        response = await client.get("/v1/public")

    assert response.status_code == 204
    assert response.headers["cache-control"] == "public, max-age=600"
    assert "pragma" not in response.headers
