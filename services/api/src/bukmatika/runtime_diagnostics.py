import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Literal, cast

import structlog
from sqlalchemy import text

from bukmatika.observability import StructuredEventLogger
from bukmatika.persistence import session_scope

DatabaseProbe = Callable[[], Awaitable[None]]
CheckStatus = Literal["ok", "failed"]
ReadinessStatus = Literal["ready", "degraded"]


@dataclass(frozen=True, slots=True)
class RuntimeCheck:
    status: CheckStatus
    code: str | None = None

    def as_payload(self) -> dict[str, str]:
        payload = {"status": self.status}
        if self.code is not None:
            payload["code"] = self.code
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeReadiness:
    status: ReadinessStatus
    checks: dict[str, RuntimeCheck]

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def as_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": {
                name: check.as_payload()
                for name, check in sorted(self.checks.items())
            },
        }


class RuntimeDiagnostics:
    """Process-local liveness evidence for database access and enabled background workers."""

    def __init__(
        self,
        *,
        database_probe: DatabaseProbe | None = None,
        logger: StructuredEventLogger | None = None,
    ) -> None:
        self._database_probe = database_probe or _probe_database
        self._logger = logger or cast(
            StructuredEventLogger,
            structlog.get_logger("bukmatika.runtime"),
        )
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._worker_failures: dict[str, tuple[str, str | None]] = {}
        self._database_failed = False
        self._stopping = False

    @property
    def worker_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return tuple(self._workers.values())

    @property
    def worker_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._workers))

    def register_worker(self, name: str, task: asyncio.Task[None]) -> None:
        if not name or name in self._workers:
            raise ValueError(f"Worker name must be unique and non-empty: {name!r}")
        self._workers[name] = task
        self._logger.info("runtime.worker.started", worker=name)
        task.add_done_callback(partial(self._worker_done, name))

    def mark_started(self, *, environment: str) -> None:
        self._logger.info(
            "runtime.started",
            environment=environment,
            workers=list(self.worker_names),
        )

    def begin_shutdown(self) -> None:
        self._stopping = True
        self._logger.info(
            "runtime.shutdown.started",
            workers=list(self.worker_names),
        )

    def mark_stopped(self) -> None:
        self._logger.info("runtime.stopped")

    async def readiness(self) -> RuntimeReadiness:
        checks: dict[str, RuntimeCheck] = {
            "database": await self._database_check(),
        }
        checks.update(
            {
                f"worker:{name}": self._worker_check(name, task)
                for name, task in self._workers.items()
            }
        )
        status: ReadinessStatus = (
            "ready" if all(check.status == "ok" for check in checks.values()) else "degraded"
        )
        return RuntimeReadiness(status=status, checks=checks)

    async def _database_check(self) -> RuntimeCheck:
        try:
            await self._database_probe()
        except Exception as exc:
            if not self._database_failed:
                self._logger.error(
                    "runtime.database.unavailable",
                    error_type=type(exc).__name__,
                )
            self._database_failed = True
            return RuntimeCheck(status="failed", code="DATABASE_UNAVAILABLE")

        if self._database_failed:
            self._logger.info("runtime.database.recovered")
        self._database_failed = False
        return RuntimeCheck(status="ok")

    def _worker_check(self, name: str, task: asyncio.Task[None]) -> RuntimeCheck:
        failure = self._worker_failures.get(name)
        if failure is not None:
            return RuntimeCheck(status="failed", code=failure[0])
        if not task.done():
            return RuntimeCheck(status="ok")
        if task.cancelled():
            return RuntimeCheck(status="failed", code="WORKER_CANCELLED")
        exception = task.exception()
        if exception is None:
            return RuntimeCheck(status="failed", code="WORKER_EXITED")
        return RuntimeCheck(status="failed", code="WORKER_CRASHED")

    def _worker_done(self, name: str, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            if self._stopping:
                self._logger.info("runtime.worker.stopped", worker=name)
                return
            self._record_worker_failure(
                name,
                code="WORKER_CANCELLED",
                error_type="CancelledError",
            )
            return

        exception = task.exception()
        if exception is None:
            if self._stopping:
                self._logger.info("runtime.worker.stopped", worker=name)
                return
            self._record_worker_failure(
                name,
                code="WORKER_EXITED",
                error_type=None,
            )
            return

        self._record_worker_failure(
            name,
            code="WORKER_CRASHED",
            error_type=type(exception).__name__,
        )

    def _record_worker_failure(
        self,
        name: str,
        *,
        code: str,
        error_type: str | None,
    ) -> None:
        self._worker_failures[name] = (code, error_type)
        event: dict[str, object] = {
            "worker": name,
            "error_code": code,
        }
        if error_type is not None:
            event["error_type"] = error_type
        self._logger.error("runtime.worker.failed", **event)


async def _probe_database() -> None:
    async with session_scope() as database_session:
        await database_session.execute(text("SELECT 1"))


__all__ = [
    "RuntimeCheck",
    "RuntimeDiagnostics",
    "RuntimeReadiness",
]
