from urllib.parse import urlsplit

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ORIGIN_HEADER = b"origin"


class BrowserOriginWriteMiddleware:
    """Reject credentialed browser mutations from any origin except the canonical web UI.

    Browser requests carry an Origin header. Non-browser local clients such as CLI tools and
    server-to-server callers may omit Origin and remain supported. CORS still governs browser
    response visibility; this middleware independently prevents cross-origin cookie-authenticated
    state changes, including same-site attacks from another localhost port.
    """

    def __init__(self, app: ASGIApp, *, allowed_origin: str) -> None:
        self._app = app
        self._allowed_origin = _normalize_origin(allowed_origin)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or _method(scope) in _SAFE_METHODS:
            await self._app(scope, receive, send)
            return

        origins = _origin_headers(scope)
        if not origins:
            await self._app(scope, receive, send)
            return

        if len(origins) != 1 or origins[0] != self._allowed_origin:
            response = JSONResponse(
                status_code=403,
                content={"detail": {"code": "ORIGIN_NOT_ALLOWED"}},
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        await self._app(scope, receive, send)


def _method(scope: Scope) -> str:
    value = scope.get("method")
    return value.upper() if isinstance(value, str) else ""


def _origin_headers(scope: Scope) -> list[str]:
    origins: list[str] = []
    for name, value in scope.get("headers", []):
        if name.lower() != _ORIGIN_HEADER:
            continue
        try:
            origins.append(_normalize_origin(value.decode("ascii")))
        except (UnicodeDecodeError, ValueError):
            origins.append("<invalid>")
    return origins


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Browser write origin must be an HTTP(S) origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Browser write origin has an invalid port") from exc

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if scheme == "http" else 443
    if port is not None and port != default_port:
        rendered_host = f"{rendered_host}:{port}"
    return f"{scheme}://{rendered_host}"


__all__ = ["BrowserOriginWriteMiddleware"]
