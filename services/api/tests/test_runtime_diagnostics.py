import asyncio
import json
from contextlib import suppress

from bukmatika.runtime_diagnostics import RuntimeDiagnostics


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **event_kw: object) -> object:
        self.events.append(("info", event, event_kw))
        return None

    def error(self, event: str, **event_kw: object) -> object:
        self.events.append(("error", event, event_kw))
        return None


def _event(logger: RecordingLogger, event_name: str) -> tuple[str, dict[str, object]]:
    for level, event, values in reversed(logger.events):
        if event == event_name:
            return level, values
    raise AssertionError(f"Missing event: {event_name}")


async def _healthy_database() -> None:
    return None


async def test_readiness_is_ready_with_database_and_live_worker() -> None:
    logger = RecordingLogger()
    stop = asyncio.Event()

    async def worker() -> None:
        await stop.wait()

    diagnostics = RuntimeDiagnostics(database_probe=_healthy_database, logger=logger)
    task = asyncio.create_task(worker(), name="test-worker")
    diagnostics.register_worker("acquisition", task)

    try:
        readiness = await diagnostics.readiness()
        assert readiness.is_ready is True
        assert readiness.as_payload() == {
            "status": "ready",
            "checks": {
                "database": {"status": "ok"},
                "worker:acquisition": {"status": "ok"},
            },
        }
    finally:
        diagnostics.begin_shutdown()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    level, values = _event(logger, "runtime.worker.stopped")
    assert level == "info"
    assert values == {"worker": "acquisition"}


async def test_database_failure_is_sanitized_and_logs_only_state_transition() -> None:
    logger = RecordingLogger()
    should_fail = True

    async def database_probe() -> None:
        if should_fail:
            raise OSError("postgresql://secret-user:secret-password@private-host/db")

    diagnostics = RuntimeDiagnostics(database_probe=database_probe, logger=logger)

    first = await diagnostics.readiness()
    second = await diagnostics.readiness()
    assert first.as_payload() == {
        "status": "degraded",
        "checks": {
            "database": {
                "status": "failed",
                "code": "DATABASE_UNAVAILABLE",
            }
        },
    }
    assert second.as_payload() == first.as_payload()

    unavailable_events = [
        values
        for _, event, values in logger.events
        if event == "runtime.database.unavailable"
    ]
    assert unavailable_events == [{"error_type": "OSError"}]
    assert "secret-password" not in json.dumps(logger.events)
    assert "private-host" not in json.dumps(logger.events)

    should_fail = False
    recovered = await diagnostics.readiness()
    assert recovered.is_ready is True
    level, values = _event(logger, "runtime.database.recovered")
    assert level == "info"
    assert values == {}


async def test_crashed_worker_degrades_readiness_without_exception_message() -> None:
    logger = RecordingLogger()

    async def worker() -> None:
        raise RuntimeError("private worker payload must never enter diagnostics")

    diagnostics = RuntimeDiagnostics(database_probe=_healthy_database, logger=logger)
    task = asyncio.create_task(worker(), name="crashing-worker")
    diagnostics.register_worker("delegation", task)

    with suppress(RuntimeError):
        await task
    await asyncio.sleep(0)

    readiness = await diagnostics.readiness()
    assert readiness.as_payload() == {
        "status": "degraded",
        "checks": {
            "database": {"status": "ok"},
            "worker:delegation": {
                "status": "failed",
                "code": "WORKER_CRASHED",
            },
        },
    }
    level, values = _event(logger, "runtime.worker.failed")
    assert level == "error"
    assert values == {
        "worker": "delegation",
        "error_code": "WORKER_CRASHED",
        "error_type": "RuntimeError",
    }
    rendered = json.dumps(logger.events)
    assert "private worker payload" not in rendered


async def test_unexpected_worker_exit_degrades_readiness() -> None:
    logger = RecordingLogger()

    async def worker() -> None:
        return None

    diagnostics = RuntimeDiagnostics(database_probe=_healthy_database, logger=logger)
    task = asyncio.create_task(worker(), name="exiting-worker")
    diagnostics.register_worker("acquisition", task)
    await task
    await asyncio.sleep(0)

    readiness = await diagnostics.readiness()
    assert readiness.as_payload()["checks"] == {
        "database": {"status": "ok"},
        "worker:acquisition": {
            "status": "failed",
            "code": "WORKER_EXITED",
        },
    }
    _, values = _event(logger, "runtime.worker.failed")
    assert values == {
        "worker": "acquisition",
        "error_code": "WORKER_EXITED",
    }


async def test_unexpected_worker_cancellation_degrades_readiness() -> None:
    logger = RecordingLogger()
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        await asyncio.Event().wait()

    diagnostics = RuntimeDiagnostics(database_probe=_healthy_database, logger=logger)
    task = asyncio.create_task(worker(), name="cancelled-worker")
    diagnostics.register_worker("delegation", task)
    await started.wait()

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    readiness = await diagnostics.readiness()
    assert readiness.as_payload()["checks"] == {
        "database": {"status": "ok"},
        "worker:delegation": {
            "status": "failed",
            "code": "WORKER_CANCELLED",
        },
    }
    _, values = _event(logger, "runtime.worker.failed")
    assert values == {
        "worker": "delegation",
        "error_code": "WORKER_CANCELLED",
        "error_type": "CancelledError",
    }


def test_worker_names_must_be_unique() -> None:
    logger = RecordingLogger()

    async def worker() -> None:
        return None

    diagnostics = RuntimeDiagnostics(database_probe=_healthy_database, logger=logger)
    first = asyncio.new_event_loop().create_task(worker())
    second = asyncio.new_event_loop().create_task(worker())
    try:
        diagnostics.register_worker("worker", first)
        try:
            diagnostics.register_worker("worker", second)
        except ValueError as exc:
            assert "unique" in str(exc)
        else:
            raise AssertionError("duplicate worker name should fail")
    finally:
        first.cancel()
        second.cancel()
        first.get_loop().run_until_complete(asyncio.gather(first, return_exceptions=True))
        second.get_loop().run_until_complete(asyncio.gather(second, return_exceptions=True))
        first.get_loop().close()
        second.get_loop().close()
