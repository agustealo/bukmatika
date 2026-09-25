import logging
import re
import sys
from time import perf_counter
from typing import Protocol, cast
from uuid import uuid4

import structlog
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.contextvars import bound_contextvars, get_contextvars, merge_contextvars
from structlog.typing import Processor

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("ascii")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class StructuredEventLogger(Protocol):
    def info(self, event: str, **event_kw: object) -> object: ...

    def error(self, event: str, **event_kw: object) -> object: ...


def configure_logging(*, level: str) -> None:
    """Configure privacy-bounded JSON logging for Bukmatika-owned logger families."""
    numeric_level = _numeric_log_level(level)
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp")
    shared_processors: list[Processor] = [
        merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Do not promote the process root logger to the application level. Libraries such as
    # HTTP clients can include full outbound URLs in their INFO records, which may contain
    # user-derived search parameters. Bukmatika-owned loggers are the explicit authority.
    _configure_owned_logger("bukmatika", handler=handler, level=numeric_level)
    _configure_owned_logger("uvicorn", handler=handler, level=numeric_level)

    error_logger = logging.getLogger("uvicorn.error")
    error_logger.handlers.clear()
    error_logger.setLevel(logging.NOTSET)
    error_logger.propagate = True
    error_logger.disabled = False

    # Uvicorn's default access line contains the raw request target, including query strings.
    # Bukmatika emits its own bounded access event instead, so disable that duplicate logger.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True

    # HTTPX/httpcore INFO records can render complete request URLs. Provider, acquisition,
    # model, and readiness failures are surfaced through Bukmatika's bounded domain events,
    # so raw transport logging is intentionally not a second diagnostic authority.
    for logger_name in ("httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = False
        logger.disabled = True


class RequestCorrelationMiddleware:
    """Bind a safe request ID and emit one privacy-bounded structured access event."""

    def __init__(self, app: ASGIApp, *, logger: StructuredEventLogger | None = None) -> None:
        self._app = app
        self._logger = logger or cast(
            StructuredEventLogger,
            structlog.get_logger("bukmatika.http"),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _request_id(scope)
        _bind_request_id_to_scope(scope, request_id)
        method = _scope_string(scope, "method", fallback="UNKNOWN")
        status_code: int | None = None
        started_at = perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                raw_status = message.get("status")
                if isinstance(raw_status, int):
                    status_code = raw_status
                _set_response_request_id(message, request_id)
            await send(message)

        with bound_contextvars(request_id=request_id):
            try:
                await self._app(scope, receive, send_with_request_id)
            except Exception as exc:
                self._logger.error(
                    "http.request.failed",
                    request_id=request_id,
                    method=method,
                    route=_route_template(scope),
                    status_code=status_code if status_code is not None else 500,
                    duration_ms=_duration_ms(started_at),
                    error_type=type(exc).__name__,
                )
                raise
            else:
                self._logger.info(
                    "http.request.completed",
                    request_id=request_id,
                    method=method,
                    route=_route_template(scope),
                    status_code=status_code if status_code is not None else 0,
                    duration_ms=_duration_ms(started_at),
                )


async def correlated_internal_server_error(request: Request, exc: Exception) -> Response:
    """Preserve the generic 500 body while exposing the request correlation ID."""
    del exc
    request_id = request_id_from_scope(request.scope)
    headers = {REQUEST_ID_HEADER: request_id} if request_id is not None else None
    return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)


def current_request_id() -> str | None:
    value = get_contextvars().get("request_id")
    return value if isinstance(value, str) else None


def request_id_from_scope(scope: Scope) -> str | None:
    state = scope.get("state")
    if not isinstance(state, dict):
        return None
    value = state.get("request_id")
    return value if isinstance(value, str) else None


def _configure_owned_logger(
    name: str,
    *,
    handler: logging.Handler,
    level: int,
) -> None:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger.disabled = False


def _numeric_log_level(level: str) -> int:
    value = logging.getLevelName(level.upper())
    if not isinstance(value, int):
        raise ValueError(f"Unsupported log level: {level}")
    return value


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name.lower() != _REQUEST_ID_HEADER_BYTES:
            continue
        try:
            candidate = value.decode("ascii")
        except UnicodeDecodeError:
            break
        if _REQUEST_ID_PATTERN.fullmatch(candidate):
            return candidate
        break
    return uuid4().hex


def _bind_request_id_to_scope(scope: Scope, request_id: str) -> None:
    state = scope.setdefault("state", {})
    state["request_id"] = request_id


def _set_response_request_id(message: Message, request_id: str) -> None:
    headers = [
        (name, value)
        for name, value in message.get("headers", [])
        if name.lower() != _REQUEST_ID_HEADER_BYTES
    ]
    headers.append((_REQUEST_ID_HEADER_BYTES, request_id.encode("ascii")))
    message["headers"] = headers


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "<unmatched>"


def _scope_string(scope: Scope, key: str, *, fallback: str) -> str:
    value = scope.get(key)
    return value if isinstance(value, str) and value else fallback


def _duration_ms(started_at: float) -> float:
    return round(max(0.0, (perf_counter() - started_at) * 1000), 3)


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestCorrelationMiddleware",
    "StructuredEventLogger",
    "configure_logging",
    "correlated_internal_server_error",
    "current_request_id",
    "request_id_from_scope",
]
