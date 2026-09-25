import asyncio
import logging
import re
import sys

import httpx
import structlog
from fastapi import FastAPI, Response

from bukmatika.observability import (
    REQUEST_ID_HEADER,
    RequestCorrelationMiddleware,
    configure_logging,
    correlated_internal_server_error,
    current_request_id,
)


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **event_kw: object) -> object:
        self.events.append(("info", event, event_kw))
        return None

    def error(self, event: str, **event_kw: object) -> object:
        self.events.append(("error", event, event_kw))
        return None


def _test_app(logger: RecordingLogger) -> FastAPI:
    app = FastAPI(exception_handlers={Exception: correlated_internal_server_error})
    app.add_middleware(RequestCorrelationMiddleware, logger=logger)

    @app.get("/items/{item_id}")
    async def item(item_id: str) -> dict[str, str | None]:
        await asyncio.sleep(0)
        return {"item_id": item_id, "request_id": current_request_id()}

    @app.get("/provided-header")
    async def provided_header() -> Response:
        return Response(headers={REQUEST_ID_HEADER: "downstream-request-id"})

    @app.get("/boom/{item_id}")
    async def boom(item_id: str) -> None:
        del item_id
        raise RuntimeError("private failure detail must not enter the request event")

    return app


def _event(logger: RecordingLogger, event_name: str) -> tuple[str, dict[str, object]]:
    for level, event, values in reversed(logger.events):
        if event == event_name:
            return level, values
    raise AssertionError(f"Missing event: {event_name}")


def _logger_state(logger: logging.Logger) -> tuple[list[logging.Handler], int, bool, bool]:
    return list(logger.handlers), logger.level, logger.propagate, logger.disabled


def _restore_logger(
    logger: logging.Logger,
    state: tuple[list[logging.Handler], int, bool, bool],
) -> None:
    handlers, level, propagate, disabled = state
    logger.handlers[:] = handlers
    logger.setLevel(level)
    logger.propagate = propagate
    logger.disabled = disabled


async def test_request_correlation_accepts_safe_caller_id_and_returns_it() -> None:
    logger = RecordingLogger()
    app = _test_app(logger)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/items/123",
            headers={REQUEST_ID_HEADER: "caller-req_123:alpha"},
        )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "caller-req_123:alpha"
    assert response.json()["request_id"] == "caller-req_123:alpha"
    level, values = _event(logger, "http.request.completed")
    assert level == "info"
    assert values["request_id"] == "caller-req_123:alpha"
    assert values["method"] == "GET"
    assert values["route"] == "/items/{item_id}"
    assert values["status_code"] == 200
    assert isinstance(values["duration_ms"], float)


async def test_request_correlation_replaces_invalid_caller_id() -> None:
    logger = RecordingLogger()
    app = _test_app(logger)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/items/123",
            headers={REQUEST_ID_HEADER: "unsafe request id with spaces"},
        )

    request_id = response.headers[REQUEST_ID_HEADER]
    assert request_id != "unsafe request id with spaces"
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert response.json()["request_id"] == request_id


async def test_request_correlation_overrides_downstream_request_id_header() -> None:
    logger = RecordingLogger()
    app = _test_app(logger)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/provided-header",
            headers={REQUEST_ID_HEADER: "canonical-request-id"},
        )

    assert response.status_code == 200
    assert response.headers.get_list(REQUEST_ID_HEADER) == ["canonical-request-id"]


async def test_request_events_use_route_template_and_never_log_query_or_path_value() -> None:
    logger = RecordingLogger()
    app = _test_app(logger)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/items/private-book-identifier?token=top-secret-query")

    assert response.status_code == 200
    _, values = _event(logger, "http.request.completed")
    assert values["route"] == "/items/{item_id}"
    rendered = repr(values)
    assert "private-book-identifier" not in rendered
    assert "top-secret-query" not in rendered
    assert "token" not in rendered


async def test_request_context_is_isolated_across_concurrent_requests() -> None:
    logger = RecordingLogger()
    app = _test_app(logger)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.get("/items/one", headers={REQUEST_ID_HEADER: "request-one"}),
            client.get("/items/two", headers={REQUEST_ID_HEADER: "request-two"}),
        )

    assert first.json()["request_id"] == "request-one"
    assert second.json()["request_id"] == "request-two"
    completed_ids = {
        values["request_id"]
        for _, event, values in logger.events
        if event == "http.request.completed"
    }
    assert completed_ids == {"request-one", "request-two"}
    assert current_request_id() is None


async def test_failed_request_preserves_request_id_without_logging_private_content() -> None:
    logger = RecordingLogger()
    app = _test_app(logger)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/boom/private-book-identifier?token=top-secret-query",
            headers={REQUEST_ID_HEADER: "failure-request"},
        )

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers[REQUEST_ID_HEADER] == "failure-request"
    level, values = _event(logger, "http.request.failed")
    assert level == "error"
    assert set(values) == {
        "request_id",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "error_type",
    }
    assert values["request_id"] == "failure-request"
    assert values["method"] == "GET"
    assert values["route"] == "/boom/{item_id}"
    assert values["status_code"] == 500
    assert values["error_type"] == "RuntimeError"
    assert isinstance(values["duration_ms"], float)
    rendered = repr(values)
    assert "private failure detail" not in rendered
    assert "private-book-identifier" not in rendered
    assert "top-secret-query" not in rendered


def test_logging_configuration_does_not_promote_raw_http_client_records(capsys) -> None:
    logger_names = (
        "",
        "bukmatika",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "httpx",
        "httpcore",
    )
    loggers = {name: logging.getLogger(name) for name in logger_names}
    states = {name: _logger_state(logger) for name, logger in loggers.items()}

    root = loggers[""]
    sentinel = logging.StreamHandler(sys.stdout)
    root.handlers.append(sentinel)
    root.setLevel(logging.INFO)

    try:
        configure_logging(level="INFO")

        assert root.handlers[-1] is sentinel
        assert root.level == logging.INFO
        assert loggers["bukmatika"].level == logging.INFO
        assert loggers["bukmatika"].propagate is False
        assert loggers["uvicorn"].level == logging.INFO
        assert loggers["uvicorn"].propagate is False
        assert loggers["httpx"].disabled is True
        assert loggers["httpcore"].disabled is True

        logging.getLogger("httpx").info(
            "HTTP Request: GET https://provider.invalid/search?"
            'q=private-search-text "HTTP/1.1 200 OK"'
        )
        logging.getLogger("httpcore").info(
            "connect_tcp.started host='provider.invalid' query='private-search-text'"
        )
        logging.getLogger("bukmatika.test").info("bounded application event")

        output = capsys.readouterr().out
        assert "bounded application event" in output
        assert "private-search-text" not in output
        assert "provider.invalid" not in output
    finally:
        for name, logger in loggers.items():
            _restore_logger(logger, states[name])
        structlog.reset_defaults()
