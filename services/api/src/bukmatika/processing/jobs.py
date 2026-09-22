import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from pathlib import Path
from typing import ClassVar, Protocol, TypeGuard
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import StoredObjectPathResolver
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import DocumentProcessingState
from bukmatika.persistence.documents import (
    DocumentRepository,
    DocumentSource,
    DocumentSourceChanged,
)
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.jobs import Job, JobLease, JobLeaseLost, JobRepository, JobStatus
from bukmatika.processing.chunking import chunk_sections
from bukmatika.processing.domain import OcrJobResponse, ParsedDocument
from bukmatika.processing.ocr import OcrExecutionError

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
logger = structlog.get_logger(__name__)


class OcrEngine(Protocol):
    name: str

    async def recognize(self, path: Path) -> ParsedDocument: ...


class OcrAssetNotFound(LookupError):
    pass


class OcrJobNotFound(LookupError):
    pass


class OcrNotEligible(RuntimeError):
    pass


class _OcrAlreadyCompleted(RuntimeError):
    pass


class OcrQueueService:
    """User-facing authority for durable OCR enqueue and status reads."""

    _job_type = "document_ocr"

    def __init__(
        self,
        settings: Settings,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._settings = settings
        self._session_scope = session_scope_factory

    async def enqueue(self, asset_id: UUID) -> OcrJobResponse:
        async with self._session_scope() as database_session:
            documents = DocumentRepository(database_session)
            jobs = JobRepository(database_session)
            source = await documents.source_for_asset(asset_id)
            if source is None:
                if await documents.asset_exists(asset_id):
                    raise OcrNotEligible("Asset has no verified stored object")
                raise OcrAssetNotFound(f"Asset {asset_id} does not exist")
            if source.format.upper() != "PDF":
                raise OcrNotEligible("OCR is currently supported only for PDF assets")

            state = await documents.get_processing_state(asset_id)
            if not _state_matches_source(state, source):
                raise OcrNotEligible("Current stored PDF has not been classified as requiring OCR")

            dedupe_key = _dedupe_key(source)
            existing = await jobs.get_by_dedupe_key(dedupe_key)
            if existing is not None and existing.status in {
                JobStatus.QUEUED.value,
                JobStatus.RUNNING.value,
                JobStatus.COMPLETED.value,
            }:
                return _job_response(existing, source.asset_id, state.status, state.error_code)

            retryable_failed = state.status == "failed" and state.processor_name == "tesseract"
            if state.status != "requires_ocr" and not retryable_failed:
                raise OcrNotEligible("Current stored PDF is not eligible for OCR")

            job = await jobs.enqueue(
                job_type=self._job_type,
                payload={
                    "asset_id": str(source.asset_id),
                    "stored_object_id": str(source.stored_object_id),
                    "source_sha256": source.sha256,
                },
                dedupe_key=dedupe_key,
                max_attempts=self._settings.ocr_max_attempts,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.DOCUMENT_OCR_REQUESTED,
                entity_type="asset",
                entity_id=source.asset_id,
                context={
                    "job_id": str(job.id),
                    "stored_object_id": str(source.stored_object_id),
                    "source_sha256": source.sha256,
                },
            )
            return _job_response(job, source.asset_id, state.status, state.error_code)

    async def get(self, job_id: UUID) -> OcrJobResponse:
        async with self._session_scope() as database_session:
            jobs = JobRepository(database_session)
            job = await jobs.get(job_id)
            if job is None or job.job_type != self._job_type:
                raise OcrJobNotFound(f"OCR job {job_id} does not exist")
            try:
                asset_id = _asset_id_from_job(job)
            except ValueError as exc:
                raise OcrJobNotFound(f"OCR job {job_id} has an invalid payload") from exc

            state = await DocumentRepository(database_session).get_processing_state(asset_id)
            processing_status = state.status if state is not None else "unknown"
            error_code = job.last_error_code
            if error_code is None and state is not None:
                error_code = state.error_code
            return _job_response(job, asset_id, processing_status, error_code)


class OcrJobWorker:
    """Durable OCR worker using the canonical PostgreSQL lease queue."""

    _job_type = "document_ocr"
    _retryable_codes: ClassVar[frozenset[str]] = frozenset({"OCR_ENGINE_TIMEOUT"})

    def __init__(
        self,
        engine: OcrEngine,
        object_store: StoredObjectPathResolver,
        settings: Settings,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        if settings.ocr_job_heartbeat_seconds >= settings.ocr_job_lease_seconds:
            raise ValueError("OCR job heartbeat must be shorter than the lease")
        self._engine = engine
        self._object_store = object_store
        self._settings = settings
        self._session_scope = session_scope_factory

    async def run(self) -> None:
        while True:
            try:
                if not await self.run_once():
                    await asyncio.sleep(self._settings.ocr_worker_poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ocr_worker_iteration_failed")
                await asyncio.sleep(self._settings.ocr_worker_poll_seconds)

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
                lease_seconds=self._settings.ocr_job_lease_seconds,
            )

    async def _execute(self, lease: JobLease) -> None:
        source: DocumentSource | None = None
        try:
            asset_id, stored_object_id, source_sha256 = _parse_payload(lease)
            source = await self._prepare_source(
                lease,
                asset_id=asset_id,
                stored_object_id=stored_object_id,
                source_sha256=source_sha256,
            )
            if source.byte_size > self._settings.processing_max_bytes:
                raise OcrExecutionError(
                    "OCR_SOURCE_TOO_LARGE",
                    "Stored PDF exceeds configured processing byte limit",
                )
            try:
                path = await self._object_store.resolve_path(source.storage_key)
            except (FileNotFoundError, ValueError) as exc:
                raise OcrExecutionError(
                    "OCR_SOURCE_UNAVAILABLE",
                    "Verified stored PDF is unavailable",
                ) from exc

            heartbeat = asyncio.create_task(self._heartbeat_loop(lease))
            try:
                parsed = await self._engine.recognize(path)
                chunks = chunk_sections(parsed.sections)
                async with self._session_scope() as database_session:
                    jobs = JobRepository(database_session)
                    if not await jobs.lease_is_owned(
                        job_id=lease.job_id,
                        claim_token=lease.claim_token,
                    ):
                        raise JobLeaseLost(
                            f"OCR job {lease.job_id} lease expired before document commit"
                        )
                    documents = DocumentRepository(database_session)
                    try:
                        document = await documents.persist_document(
                            source=source,
                            parsed=parsed,
                            chunks=chunks,
                        )
                        await documents.set_processing_state(
                            source=source,
                            processor_name=parsed.parser_name,
                            processor_version=parsed.parser_version,
                            status="completed",
                        )
                    except DocumentSourceChanged as exc:
                        raise OcrExecutionError("OCR_SOURCE_CHANGED", str(exc)) from exc
                    await InteractionEventRepository(database_session).record(
                        SemanticEventType.DOCUMENT_OCR_COMPLETED,
                        entity_type="document",
                        entity_id=document.id,
                        context={
                            "asset_id": str(source.asset_id),
                            "job_id": str(lease.job_id),
                            "stored_object_id": str(source.stored_object_id),
                            "source_sha256": source.sha256,
                            "processor_name": parsed.parser_name,
                            "processor_version": parsed.parser_version,
                            "section_count": document.section_count,
                            "chunk_count": document.chunk_count,
                        },
                    )
                    await jobs.complete(
                        job_id=lease.job_id,
                        claim_token=lease.claim_token,
                    )
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError, JobLeaseLost):
                    await heartbeat
        except asyncio.CancelledError:
            raise
        except _OcrAlreadyCompleted:
            await self._complete_job(lease)
        except JobLeaseLost:
            logger.info("ocr_worker_lease_lost", job_id=str(lease.job_id))
        except OcrExecutionError as exc:
            try:
                if exc.error_code in self._retryable_codes and lease.attempt_count < lease.max_attempts:
                    await self._retry_job(lease, source, exc.error_code, exc.detail)
                else:
                    await self._fail_job(lease, source, exc.error_code, exc.detail)
            except JobLeaseLost:
                logger.info("ocr_worker_lease_lost", job_id=str(lease.job_id))
        except (KeyError, TypeError, ValueError) as exc:
            try:
                await self._fail_job(
                    lease,
                    source,
                    "INVALID_JOB_PAYLOAD",
                    type(exc).__name__,
                )
            except JobLeaseLost:
                logger.info("ocr_worker_lease_lost", job_id=str(lease.job_id))
        except Exception as exc:
            logger.exception("ocr_worker_execution_failed", job_id=str(lease.job_id))
            try:
                await self._fail_job(
                    lease,
                    source,
                    "OCR_INTERNAL_ERROR",
                    type(exc).__name__,
                )
            except JobLeaseLost:
                logger.info("ocr_worker_lease_lost", job_id=str(lease.job_id))

    async def _prepare_source(
        self,
        lease: JobLease,
        *,
        asset_id: UUID,
        stored_object_id: UUID,
        source_sha256: str,
    ) -> DocumentSource:
        async with self._session_scope() as database_session:
            documents = DocumentRepository(database_session)
            source = await documents.source_for_asset(asset_id)
            if source is None:
                raise OcrExecutionError("OCR_SOURCE_CHANGED", "OCR source asset is unavailable")
            if (
                source.stored_object_id != stored_object_id
                or source.sha256 != source_sha256
                or source.format.upper() != "PDF"
            ):
                raise OcrExecutionError(
                    "OCR_SOURCE_CHANGED",
                    "Stored PDF changed after the OCR job was enqueued",
                )
            state = await documents.get_processing_state(asset_id)
            if not _state_matches_source(state, source):
                raise OcrExecutionError(
                    "OCR_SOURCE_CHANGED",
                    "Processing state no longer belongs to the enqueued stored PDF",
                )
            if state.status == "completed":
                raise _OcrAlreadyCompleted
            if state.status == "processing" and state.processor_name != self._engine.name:
                raise OcrExecutionError(
                    "OCR_NOT_ELIGIBLE",
                    "Current stored PDF is owned by another processing authority",
                )
            if state.status not in {"requires_ocr", "failed", "processing"}:
                raise OcrExecutionError(
                    "OCR_NOT_ELIGIBLE",
                    f"Current processing state {state.status!r} is not OCR-eligible",
                )
            await documents.set_processing_state(
                source=source,
                processor_name=self._engine.name,
                processor_version="pending",
                status="processing",
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.DOCUMENT_OCR_STARTED,
                entity_type="asset",
                entity_id=source.asset_id,
                context={
                    "job_id": str(lease.job_id),
                    "attempt_count": lease.attempt_count,
                    "recovered_from_expired_lease": lease.recovered_from_expired_lease,
                    "stored_object_id": str(source.stored_object_id),
                    "source_sha256": source.sha256,
                },
            )
            return source

    async def _heartbeat_loop(self, lease: JobLease) -> None:
        while True:
            await asyncio.sleep(self._settings.ocr_job_heartbeat_seconds)
            async with self._session_scope() as database_session:
                await JobRepository(database_session).heartbeat(
                    job_id=lease.job_id,
                    claim_token=lease.claim_token,
                    lease_seconds=self._settings.ocr_job_lease_seconds,
                )

    async def _complete_job(self, lease: JobLease) -> None:
        async with self._session_scope() as database_session:
            await JobRepository(database_session).complete(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
            )

    async def _retry_job(
        self,
        lease: JobLease,
        source: DocumentSource | None,
        error_code: str,
        detail: str,
    ) -> None:
        exponent = max(lease.attempt_count - 1, 0)
        delay = min(
            self._settings.ocr_retry_base_seconds * (2**exponent),
            self._settings.ocr_retry_max_seconds,
        )
        async with self._session_scope() as database_session:
            jobs = JobRepository(database_session)
            job = await jobs.retry(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                error_code=error_code,
                error_detail=detail[:2000],
                delay_seconds=delay,
            )
            if source is not None:
                with suppress(DocumentSourceChanged):
                    await DocumentRepository(database_session).set_processing_state(
                        source=source,
                        processor_name=self._engine.name,
                        processor_version="pending",
                        status="requires_ocr",
                        error_code=error_code,
                        error_detail=detail[:2000],
                    )
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.DOCUMENT_OCR_RETRY_SCHEDULED,
                    entity_type="asset",
                    entity_id=source.asset_id,
                    context={
                        "job_id": str(job.id),
                        "attempt_count": job.attempt_count,
                        "delay_seconds": delay,
                        "error_code": error_code,
                    },
                )

    async def _fail_job(
        self,
        lease: JobLease,
        source: DocumentSource | None,
        error_code: str,
        detail: str,
    ) -> None:
        async with self._session_scope() as database_session:
            jobs = JobRepository(database_session)
            await jobs.fail(
                job_id=lease.job_id,
                claim_token=lease.claim_token,
                error_code=error_code,
                error_detail=detail[:2000],
            )
            if source is not None:
                with suppress(DocumentSourceChanged):
                    await DocumentRepository(database_session).set_processing_state(
                        source=source,
                        processor_name=self._engine.name,
                        processor_version="pending",
                        status="failed",
                        error_code=error_code,
                        error_detail=detail[:2000],
                    )
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.DOCUMENT_OCR_FAILED,
                    entity_type="asset",
                    entity_id=source.asset_id,
                    context={
                        "job_id": str(lease.job_id),
                        "attempt_count": lease.attempt_count,
                        "error_code": error_code,
                    },
                )


def _parse_payload(lease: JobLease) -> tuple[UUID, UUID, str]:
    asset_id = UUID(str(lease.payload["asset_id"]))
    stored_object_id = UUID(str(lease.payload["stored_object_id"]))
    source_sha256 = str(lease.payload["source_sha256"])
    if len(source_sha256) != 64:
        raise ValueError("source_sha256 must be a SHA-256 hex digest")
    int(source_sha256, 16)
    return asset_id, stored_object_id, source_sha256


def _asset_id_from_job(job: Job) -> UUID:
    try:
        return UUID(str(job.payload["asset_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("OCR job payload has no valid asset_id") from exc


def _state_matches_source(
    state: DocumentProcessingState | None,
    source: DocumentSource,
) -> TypeGuard[DocumentProcessingState]:
    return bool(
        state is not None
        and state.stored_object_id == source.stored_object_id
        and state.source_sha256 == source.sha256
    )


def _dedupe_key(source: DocumentSource) -> str:
    return f"document_ocr:{source.asset_id}:{source.stored_object_id}:{source.sha256}"


def _job_response(
    job: Job,
    asset_id: UUID,
    processing_status: str,
    error_code: str | None,
) -> OcrJobResponse:
    return OcrJobResponse(
        job_id=job.id,
        asset_id=asset_id,
        job_status=job.status,
        processing_status=processing_status,
        error_code=error_code,
    )
