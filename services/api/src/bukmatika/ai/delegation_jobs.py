import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_domain import (
    DelegationNotFound,
    DelegationResponse,
    DelegationStatus,
    DelegationStopRequested,
)
from bukmatika.ai.delegation_runtime import DelegationRuntimeService
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegations import DelegationRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.jobs import Job, JobLease, JobLeaseLost, JobRepository, JobStatus

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
logger = structlog.get_logger(__name__)
DELEGATION_JOB_TYPE = "ai_delegation"
DELEGATION_DISPATCH_FAILURE_CODE = "DELEGATION_DISPATCH_FAILED"


def delegation_job_dedupe_key(delegation_id: UUID) -> str:
    return f"ai-delegation:{delegation_id}"


async def enqueue_delegation_job_in_session(
    database_session: AsyncSession,
    *,
    settings: Settings,
    principal_id: UUID,
    delegation_id: UUID,
) -> Job:
    job = await JobRepository(database_session).enqueue(
        job_type=DELEGATION_JOB_TYPE,
        payload={
            "principal_id": str(principal_id),
            "delegation_id": str(delegation_id),
        },
        dedupe_key=delegation_job_dedupe_key(delegation_id),
        max_attempts=settings.delegation_job_max_attempts,
    )
    await InteractionEventRepository(database_session).record(
        SemanticEventType.AI_DELEGATION_DISPATCH_QUEUED,
        principal_id=principal_id,
        entity_type="ai_delegation",
        entity_id=delegation_id,
        context={
            "delegation_id": str(delegation_id),
            "job_id": str(job.id),
            "job_status": job.status,
        },
    )
    return job


class DelegationJobWorker:
    """Durably dispatch bounded delegation state through the canonical PostgreSQL job queue."""

    def __init__(
        self,
        settings: Settings,
        *,
        runtime_service: DelegationRuntimeService | None = None,
        control_service: DelegationControlService | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        if settings.delegation_job_heartbeat_seconds >= settings.delegation_job_lease_seconds:
            raise ValueError("Delegation job heartbeat must be shorter than the lease")
        if settings.delegation_job_lease_seconds <= 60:
            raise ValueError("Delegation job lease must outlive the delegated attempt claim lease")
        self._settings = settings
        self._session_scope = session_scope_factory
        self._control = control_service or DelegationControlService(
            session_scope_factory=session_scope_factory
        )
        self._runtime = runtime_service or DelegationRuntimeService(
            control_service=self._control,
            session_scope_factory=session_scope_factory,
        )

    async def run(self) -> None:
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(self._settings.delegation_worker_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("delegation_worker_iteration_failed")
                await asyncio.sleep(self._settings.delegation_worker_poll_seconds)

    async def run_once(self) -> bool:
        lease = await self._claim()
        if lease is None:
            return False
        await self._execute(lease)
        return True

    async def _claim(self) -> JobLease | None:
        async with self._session_scope() as database_session:
            return await JobRepository(database_session).claim_next(
                job_type=DELEGATION_JOB_TYPE,
                lease_seconds=self._settings.delegation_job_lease_seconds,
            )

    async def _execute(self, lease: JobLease) -> None:
        try:
            principal_id = UUID(str(lease.payload["principal_id"]))
            delegation_id = UUID(str(lease.payload["delegation_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            await self._fail_job(
                lease,
                error_code="INVALID_JOB_PAYLOAD",
                detail=type(exc).__name__,
            )
            return

        heartbeat = asyncio.create_task(self._heartbeat_loop(lease))
        try:
            while True:
                if not await self._lease_is_owned(lease):
                    raise JobLeaseLost(f"Delegation job {lease.job_id} lease is no longer owned")

                try:
                    before = await self._control.get(
                        principal_id=principal_id,
                        delegation_id=delegation_id,
                    )
                except DelegationNotFound:
                    await self._fail_job(
                        lease,
                        error_code="DELEGATION_NOT_FOUND",
                        detail="Delegation disappeared after dispatch was queued",
                    )
                    return

                if await self._settle_terminal_or_stop(
                    lease,
                    principal_id=principal_id,
                    delegation=before,
                ):
                    return

                if before.status is not DelegationStatus.RUNNING:
                    await self._retry_or_fail_dispatch(
                        lease,
                        principal_id=principal_id,
                        delegation_id=delegation_id,
                        error_code="DELEGATION_NOT_RUNNING",
                        detail=f"Dispatched delegation is {before.status.value}",
                    )
                    return

                attempts_before = before.attempts_used
                try:
                    result = await self._runtime.run_once(
                        principal_id=principal_id,
                        delegation_id=delegation_id,
                    )
                except asyncio.CancelledError:
                    raise
                except DelegationStopRequested:
                    stopped = await self._runtime.acknowledge_stop(
                        principal_id=principal_id,
                        delegation_id=delegation_id,
                    )
                    await self._cancel_job(lease)
                    logger.info(
                        "delegation_worker_stop_acknowledged",
                        delegation_id=str(stopped.delegation_id),
                        job_id=str(lease.job_id),
                    )
                    return
                except Exception as exc:
                    try:
                        after = await self._control.get(
                            principal_id=principal_id,
                            delegation_id=delegation_id,
                        )
                    except DelegationNotFound:
                        await self._fail_job(
                            lease,
                            error_code="DELEGATION_NOT_FOUND",
                            detail="Delegation disappeared during delegated execution",
                        )
                        return
                    if await self._settle_terminal_or_stop(
                        lease,
                        principal_id=principal_id,
                        delegation=after,
                    ):
                        return
                    if (
                        after.status is DelegationStatus.RUNNING
                        and after.attempts_used > attempts_before
                    ):
                        continue
                    await self._retry_or_fail_dispatch(
                        lease,
                        principal_id=principal_id,
                        delegation_id=delegation_id,
                        error_code=str(getattr(exc, "code", type(exc).__name__))[:64],
                        detail=str(exc)[:2000] or type(exc).__name__,
                    )
                    return

                if await self._settle_terminal_or_stop(
                    lease,
                    principal_id=principal_id,
                    delegation=result.delegation,
                ):
                    return
        except JobLeaseLost:
            logger.info(
                "delegation_worker_lease_lost",
                job_id=str(lease.job_id),
                delegation_id=str(delegation_id),
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, JobLeaseLost):
                await heartbeat

    async def _settle_terminal_or_stop(
        self,
        lease: JobLease,
        *,
        principal_id: UUID,
        delegation: DelegationResponse,
    ) -> bool:
        if delegation.status is DelegationStatus.COMPLETED:
            await self._complete_job(lease)
            return True
        if delegation.status in {DelegationStatus.STOPPED, DelegationStatus.CANCELLED}:
            await self._cancel_job(lease)
            return True
        if delegation.status is DelegationStatus.FAILED:
            await self._fail_job(
                lease,
                error_code=delegation.failure_code or "DELEGATION_FAILED",
                detail="Delegation entered a terminal failure state",
            )
            return True
        if delegation.status is DelegationStatus.STOP_REQUESTED:
            stopped = await self._runtime.acknowledge_stop(
                principal_id=principal_id,
                delegation_id=delegation.delegation_id,
            )
            await self._cancel_job(lease)
            logger.info(
                "delegation_worker_stop_acknowledged",
                delegation_id=str(stopped.delegation_id),
                job_id=str(lease.job_id),
            )
            return True
        return False

    async def _lease_is_owned(self, lease: JobLease) -> bool:
        async with self._session_scope() as database_session:
            return await JobRepository(database_session).lease_is_owned(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
            )

    async def _heartbeat_loop(self, lease: JobLease) -> None:
        while True:
            await asyncio.sleep(self._settings.delegation_job_heartbeat_seconds)
            async with self._session_scope() as database_session:
                await JobRepository(database_session).heartbeat(
                    job_id=lease.job_id,
                    claim_token=lease.claim_token,
                    lease_seconds=self._settings.delegation_job_lease_seconds,
                )

    async def _complete_job(self, lease: JobLease) -> None:
        async with self._session_scope() as database_session:
            await JobRepository(database_session).complete(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
            )

    async def _cancel_job(self, lease: JobLease) -> None:
        async with self._session_scope() as database_session:
            await JobRepository(database_session).cancel_owned(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
            )

    async def _fail_job(self, lease: JobLease, *, error_code: str, detail: str) -> None:
        async with self._session_scope() as database_session:
            await JobRepository(database_session).fail(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                error_code=error_code[:64],
                error_detail=detail[:2000],
            )

    async def _retry_or_fail_dispatch(
        self,
        lease: JobLease,
        *,
        principal_id: UUID,
        delegation_id: UUID,
        error_code: str,
        detail: str,
    ) -> None:
        async with self._session_scope() as database_session:
            job = await JobRepository(database_session).retry(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                error_code=error_code[:64],
                error_detail=detail[:2000],
                delay_seconds=self._settings.delegation_retry_seconds,
            )
            if job.status != JobStatus.FAILED.value:
                return

            repository = DelegationRepository(database_session)
            try:
                delegation = await repository.get(
                    principal_id=principal_id,
                    delegation_id=delegation_id,
                    lock=True,
                )
            except DelegationNotFound:
                return
            if delegation.status == DelegationStatus.RUNNING.value:
                delegation.status = DelegationStatus.FAILED.value
                delegation.failure_code = DELEGATION_DISPATCH_FAILURE_CODE
                delegation.completed_at = datetime.now(UTC)
                await database_session.flush()
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.AI_DELEGATION_DISPATCH_FAILED,
                    principal_id=principal_id,
                    entity_type="ai_delegation",
                    entity_id=delegation.id,
                    context={
                        "delegation_id": str(delegation.id),
                        "job_id": str(job.id),
                        "worker_error_code": error_code[:64],
                    },
                )
