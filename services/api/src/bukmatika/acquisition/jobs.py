import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import (
    AcquisitionJobResponse,
    AcquisitionResponse,
    AcquisitionStatus,
)
from bukmatika.acquisition.service import (
    AcquisitionCancelled,
    AcquisitionDenied,
    AcquisitionExecutionError,
    AcquisitionLeaseLost,
    AcquisitionService,
    AssetNotFound,
)
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition import AcquisitionRepository, AcquisitionStateConflict
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.jobs import JobLease, JobLeaseLost, JobRepository, JobStatus

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
logger = structlog.get_logger(__name__)


class AcquisitionNotFound(LookupError):
    pass


class AcquisitionQueueService:
    """User-facing authority for enqueueing, observing, and cancelling acquisitions."""

    _job_type = "acquisition"
    _active_states = {
        AcquisitionStatus.RESOLVING.value,
        AcquisitionStatus.DOWNLOADING.value,
        AcquisitionStatus.VERIFYING.value,
    }
    _terminal_job_states = {
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELLED.value,
    }

    def __init__(
        self,
        settings: Settings,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._settings = settings
        self._session_scope = session_scope_factory

    async def enqueue(self, asset_id: UUID) -> AcquisitionJobResponse:
        async with self._session_scope() as database_session:
            acquisitions = AcquisitionRepository(database_session)
            jobs = JobRepository(database_session)
            asset = await acquisitions.get_asset(asset_id)
            if asset is None:
                raise AssetNotFound(f"Asset {asset_id} does not exist")
            acquisition = await acquisitions.create_or_get_acquisition(asset)
            dedupe_key = self._dedupe_key(acquisition.id)

            if acquisition.status == AcquisitionStatus.STORED.value:
                return AcquisitionJobResponse(
                    acquisition_id=acquisition.id,
                    asset_id=asset.id,
                    acquisition_status=AcquisitionStatus.STORED,
                    job_status=JobStatus.COMPLETED.value,
                )
            if acquisition.status == AcquisitionStatus.QUARANTINED.value:
                raise AcquisitionStateConflict(
                    "Quarantined acquisition requires refreshed source evidence before retry"
                )

            existing_job = await jobs.get_by_dedupe_key(dedupe_key)
            orphaned_active = acquisition.status in self._active_states and (
                existing_job is None or existing_job.status in self._terminal_job_states
            )
            if orphaned_active:
                recovered = await acquisitions.recover_expired_attempt(asset.id)
                if recovered is not None:
                    acquisition = recovered

            job = await jobs.enqueue(
                job_type=self._job_type,
                payload={"asset_id": str(asset.id), "acquisition_id": str(acquisition.id)},
                dedupe_key=dedupe_key,
                max_attempts=self._settings.acquisition_max_attempts,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACQUISITION_REQUESTED,
                entity_type="asset",
                entity_id=asset.id,
                context={
                    "acquisition_id": str(acquisition.id),
                    "job_id": str(job.id),
                },
            )
            return AcquisitionJobResponse(
                acquisition_id=acquisition.id,
                asset_id=asset.id,
                job_id=job.id,
                acquisition_status=AcquisitionStatus(acquisition.status),
                job_status=job.status,
            )

    async def get(self, acquisition_id: UUID) -> AcquisitionResponse:
        async with self._session_scope() as database_session:
            repository = AcquisitionRepository(database_session)
            acquisition = await repository.get_acquisition(acquisition_id)
            if acquisition is None:
                raise AcquisitionNotFound(f"Acquisition {acquisition_id} does not exist")
            storage_key: str | None = None
            sha256 = acquisition.sha256
            byte_size = acquisition.bytes_received
            media_type = acquisition.media_type
            if acquisition.stored_object_id is not None:
                stored = await repository.get_stored_object(acquisition.stored_object_id)
                if stored is not None:
                    storage_key = stored.storage_key
                    sha256 = stored.sha256
                    byte_size = stored.byte_size
                    media_type = stored.media_type
            return AcquisitionResponse(
                acquisition_id=acquisition.id,
                asset_id=acquisition.asset_id,
                status=AcquisitionStatus(acquisition.status),
                sha256=sha256,
                byte_size=byte_size,
                media_type=media_type,
                storage_key=storage_key,
                error_code=acquisition.error_code,
            )

    async def cancel(self, acquisition_id: UUID) -> AcquisitionResponse:
        async with self._session_scope() as database_session:
            acquisitions = AcquisitionRepository(database_session)
            jobs = JobRepository(database_session)
            acquisition = await acquisitions.get_acquisition(acquisition_id)
            if acquisition is None:
                raise AcquisitionNotFound(f"Acquisition {acquisition_id} does not exist")
            if acquisition.status in {
                AcquisitionStatus.STORED.value,
                AcquisitionStatus.QUARANTINED.value,
                AcquisitionStatus.CANCELLED.value,
            }:
                return AcquisitionResponse(
                    acquisition_id=acquisition.id,
                    asset_id=acquisition.asset_id,
                    status=AcquisitionStatus(acquisition.status),
                    error_code=acquisition.error_code,
                )

            acquisition = await acquisitions.request_cancel(acquisition_id)
            await jobs.cancel_if_queued(dedupe_key=self._dedupe_key(acquisition_id))
            events = InteractionEventRepository(database_session)
            await events.record(
                SemanticEventType.ACQUISITION_CANCEL_REQUESTED,
                entity_type="asset",
                entity_id=acquisition.asset_id,
                context={"acquisition_id": str(acquisition.id)},
            )
            if acquisition.status == AcquisitionStatus.CANCELLED.value:
                await events.record(
                    SemanticEventType.ACQUISITION_CANCELLED,
                    entity_type="asset",
                    entity_id=acquisition.asset_id,
                    context={"acquisition_id": str(acquisition.id)},
                )
            return AcquisitionResponse(
                acquisition_id=acquisition.id,
                asset_id=acquisition.asset_id,
                status=AcquisitionStatus(acquisition.status),
                error_code=acquisition.error_code,
            )

    @staticmethod
    def _dedupe_key(acquisition_id: UUID) -> str:
        return f"acquisition:{acquisition_id}"


class AcquisitionJobWorker:
    """Single-job worker loop. Multiple processes coordinate through PostgreSQL leases."""

    _job_type = "acquisition"
    _retryable_codes = {"REMOTE_DOWNLOAD_FAILED", "STORAGE_FAILED"}

    def __init__(
        self,
        acquisition_service: AcquisitionService,
        settings: Settings,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        if settings.acquisition_job_heartbeat_seconds >= settings.acquisition_job_lease_seconds:
            raise ValueError("Acquisition job heartbeat must be shorter than the lease")
        self._acquisition_service = acquisition_service
        self._settings = settings
        self._session_scope = session_scope_factory

    async def run(self) -> None:
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(self._settings.acquisition_worker_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("acquisition_worker_iteration_failed")
                await asyncio.sleep(self._settings.acquisition_worker_poll_seconds)

    async def run_once(self) -> bool:
        lease = await self._claim()
        if lease is None:
            return False
        await self._execute(lease)
        return True

    async def _claim(self) -> JobLease | None:
        async with self._session_scope() as database_session:
            return await JobRepository(database_session).claim_next(
                job_type=self._job_type,
                lease_seconds=self._settings.acquisition_job_lease_seconds,
            )

    async def _execute(self, lease: JobLease) -> None:
        try:
            asset_id = UUID(str(lease.payload["asset_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            await self._fail_job(lease, "INVALID_JOB_PAYLOAD", type(exc).__name__)
            return

        if lease.recovered_from_expired_lease:
            async with self._session_scope() as database_session:
                await AcquisitionRepository(database_session).recover_expired_attempt(asset_id)

        async def lease_probe() -> bool:
            async with self._session_scope() as database_session:
                return await JobRepository(database_session).lease_is_owned(
                    job_id=lease.job_id,
                    claim_token=lease.claim_token,
                )

        async def lease_guard(database_session: AsyncSession) -> None:
            await JobRepository(database_session).heartbeat(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                lease_seconds=self._settings.acquisition_job_lease_seconds,
            )

        heartbeat = asyncio.create_task(self._heartbeat_loop(lease))
        try:
            await self._acquisition_service.acquire(
                asset_id,
                lease_probe=lease_probe,
                lease_guard=lease_guard,
            )
        except asyncio.CancelledError:
            raise
        except (AcquisitionLeaseLost, JobLeaseLost):
            logger.info(
                "acquisition_worker_lease_lost",
                job_id=str(lease.job_id),
                asset_id=str(asset_id),
            )
        except AcquisitionCancelled:
            await self._cancel_job(lease)
        except AcquisitionDenied as exc:
            await self._fail_job(lease, "RIGHTS_DENIED", exc.reason)
        except AssetNotFound:
            await self._fail_job(lease, "ASSET_NOT_FOUND", "Asset no longer exists")
        except AcquisitionStateConflict as exc:
            await self._fail_job(lease, "ACQUISITION_STATE_CONFLICT", str(exc))
        except AcquisitionExecutionError as exc:
            if exc.error_code in self._retryable_codes:
                await self._retry_job(lease, exc.error_code, exc.detail, asset_id)
            else:
                await self._fail_job(lease, exc.error_code, exc.detail)
        else:
            await self._complete_job(lease)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, JobLeaseLost):
                await heartbeat

    async def _heartbeat_loop(self, lease: JobLease) -> None:
        while True:
            await asyncio.sleep(self._settings.acquisition_job_heartbeat_seconds)
            async with self._session_scope() as database_session:
                await JobRepository(database_session).heartbeat(
                    job_id=lease.job_id,
                    claim_token=lease.claim_token,
                    lease_seconds=self._settings.acquisition_job_lease_seconds,
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

    async def _fail_job(self, lease: JobLease, error_code: str, detail: str) -> None:
        async with self._session_scope() as database_session:
            await JobRepository(database_session).fail(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                error_code=error_code,
                error_detail=detail,
            )

    async def _retry_job(
        self,
        lease: JobLease,
        error_code: str,
        detail: str,
        asset_id: UUID,
    ) -> None:
        exponent = max(lease.attempt_count - 1, 0)
        delay = min(
            self._settings.acquisition_retry_base_seconds * (2**exponent),
            self._settings.acquisition_retry_max_seconds,
        )
        async with self._session_scope() as database_session:
            job = await JobRepository(database_session).retry(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                error_code=error_code,
                error_detail=detail,
                delay_seconds=delay,
            )
            if job.status == JobStatus.QUEUED.value:
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.ACQUISITION_RETRY_SCHEDULED,
                    entity_type="asset",
                    entity_id=asset_id,
                    context={
                        "job_id": str(job.id),
                        "attempt_count": job.attempt_count,
                        "delay_seconds": delay,
                        "error_code": error_code,
                    },
                )
