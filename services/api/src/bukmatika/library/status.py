from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.domain import (
    AssetStatusResponse,
    LibraryAssetAggregateStatus,
    LibraryAssetStage,
    LibraryAssetStatusItem,
    LibraryStatusResponse,
    LibraryStatusSummary,
)
from bukmatika.library.service import LibraryService
from bukmatika.persistence import session_scope
from bukmatika.persistence.jobs import Job, JobStatus
from bukmatika.persistence.library import LibraryRepository
from bukmatika.persistence.models import Acquisition, LibraryEntry

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_ACTIVE_ACQUISITION_STATES = frozenset({"queued", "resolving", "downloading", "verifying"})


@dataclass(frozen=True, slots=True)
class _OwnedAsset:
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    edition_id: UUID
    edition_title: str
    status: AssetStatusResponse


class LibraryStatusService:
    """Read-only consumer projection over canonical library pipeline authorities."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory
        self._library = LibraryService(session_scope_factory=session_scope_factory)

    async def snapshot(self, *, principal_id: UUID) -> LibraryStatusResponse:
        async with self._session_scope() as database_session:
            entries = await LibraryRepository(database_session).library_entries(principal_id)
        work_entry_ids = {
            entry.work_id: entry.id for entry in entries if entry.edition_id is None
        }
        edition_entries = {
            entry.edition_id: entry.id for entry in entries if entry.edition_id is not None
        }

        owned_assets: list[_OwnedAsset] = []
        for work_id in sorted({entry.work_id for entry in entries}, key=str):
            dossier = await self._library.dossier_for_work(
                principal_id=principal_id,
                work_id=work_id,
            )
            work_entry_id = work_entry_ids.get(work_id)
            for edition in dossier.editions:
                library_entry_id = edition_entries.get(edition.edition_id) or work_entry_id
                if library_entry_id is None:
                    continue
                for status in edition.assets:
                    owned_assets.append(
                        _OwnedAsset(
                            library_entry_id=library_entry_id,
                            work_id=work_id,
                            work_title=dossier.title,
                            edition_id=edition.edition_id,
                            edition_title=edition.title,
                            status=status,
                        )
                    )

        items: list[LibraryAssetStatusItem] = []
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            for owned in owned_assets:
                acquisition = await repository.acquisition_for_asset(owned.status.asset_id)
                acquisition_job = await repository.latest_job_for_asset(
                    job_type="acquisition",
                    asset_id=owned.status.asset_id,
                )
                ocr_job = await repository.latest_job_for_asset(
                    job_type="document_ocr",
                    asset_id=owned.status.asset_id,
                )
                aggregate_status, stage, status_code = _classify(
                    owned.status,
                    acquisition=acquisition,
                    acquisition_job=acquisition_job,
                    ocr_job=ocr_job,
                )
                items.append(
                    LibraryAssetStatusItem(
                        library_entry_id=owned.library_entry_id,
                        work_id=owned.work_id,
                        work_title=owned.work_title,
                        edition_id=owned.edition_id,
                        edition_title=owned.edition_title,
                        asset_id=owned.status.asset_id,
                        format=owned.status.format,
                        media_type=owned.status.media_type,
                        aggregate_status=aggregate_status,
                        stage=stage,
                        status_code=status_code,
                        rights_state=owned.status.rights_state,
                        acquisition_allowed=owned.status.acquisition_allowed,
                        acquisition_id=owned.status.acquisition_id,
                        acquisition_status=owned.status.acquisition_status,
                        acquisition_job_id=(
                            acquisition_job.id if acquisition_job is not None else None
                        ),
                        acquisition_job_status=(
                            acquisition_job.status if acquisition_job is not None else None
                        ),
                        acquisition_error_code=(
                            acquisition.error_code if acquisition is not None else None
                        ),
                        processing_status=owned.status.processing_status,
                        processing_error_code=owned.status.processing_error_code,
                        ocr_job_id=ocr_job.id if ocr_job is not None else None,
                        ocr_job_status=ocr_job.status if ocr_job is not None else None,
                        ocr_error_code=(
                            ocr_job.last_error_code if ocr_job is not None else None
                        ),
                        document_id=owned.status.document_id,
                    )
                )

        items.sort(
            key=lambda item: (
                _status_order(item.aggregate_status),
                item.work_title.casefold(),
                item.edition_title.casefold(),
                item.format,
                str(item.asset_id),
            )
        )
        return LibraryStatusResponse(
            summary=_summary(items),
            items=items,
        )


def _classify(
    status: AssetStatusResponse,
    *,
    acquisition: Acquisition | None,
    acquisition_job: Job | None,
    ocr_job: Job | None,
) -> tuple[LibraryAssetAggregateStatus, LibraryAssetStage, str]:
    if status.document_id is not None:
        return (
            LibraryAssetAggregateStatus.READY,
            LibraryAssetStage.READY,
            "DOCUMENT_READY",
        )

    if status.processing_status == "failed":
        if ocr_job is not None:
            return (
                LibraryAssetAggregateStatus.FAILED,
                LibraryAssetStage.OCR,
                status.processing_error_code or ocr_job.last_error_code or "OCR_FAILED",
            )
        return (
            LibraryAssetAggregateStatus.FAILED,
            LibraryAssetStage.PROCESSING,
            status.processing_error_code or "PROCESSING_FAILED",
        )

    if ocr_job is not None and ocr_job.status == JobStatus.FAILED.value:
        return (
            LibraryAssetAggregateStatus.FAILED,
            LibraryAssetStage.OCR,
            ocr_job.last_error_code or "OCR_FAILED",
        )

    if status.processing_status == "requires_ocr":
        if ocr_job is not None and ocr_job.status in {
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
        }:
            return (
                LibraryAssetAggregateStatus.IN_PROGRESS,
                LibraryAssetStage.OCR,
                "OCR_IN_PROGRESS",
            )
        if ocr_job is not None and ocr_job.status == JobStatus.COMPLETED.value:
            return (
                LibraryAssetAggregateStatus.FAILED,
                LibraryAssetStage.OCR,
                "OCR_COMPLETED_DOCUMENT_MISSING",
            )
        if ocr_job is not None and ocr_job.status == JobStatus.CANCELLED.value:
            return (
                LibraryAssetAggregateStatus.NEEDS_ACTION,
                LibraryAssetStage.OCR,
                "OCR_CANCELLED",
            )
        return (
            LibraryAssetAggregateStatus.NEEDS_ACTION,
            LibraryAssetStage.OCR,
            "OCR_REQUIRED",
        )

    if status.processing_status == "processing":
        return (
            LibraryAssetAggregateStatus.IN_PROGRESS,
            LibraryAssetStage.PROCESSING,
            "PROCESSING_IN_PROGRESS",
        )

    if status.processing_status == "completed":
        return (
            LibraryAssetAggregateStatus.FAILED,
            LibraryAssetStage.PROCESSING,
            "PROCESSING_COMPLETED_DOCUMENT_MISSING",
        )

    if status.acquisition_status == "failed":
        return (
            LibraryAssetAggregateStatus.FAILED,
            LibraryAssetStage.ACQUISITION,
            (acquisition.error_code if acquisition is not None else None) or "ACQUISITION_FAILED",
        )

    if status.acquisition_status == "quarantined":
        return (
            LibraryAssetAggregateStatus.NEEDS_ACTION,
            LibraryAssetStage.ACQUISITION,
            "ACQUISITION_QUARANTINED",
        )

    if status.acquisition_status in _ACTIVE_ACQUISITION_STATES:
        return (
            LibraryAssetAggregateStatus.IN_PROGRESS,
            LibraryAssetStage.ACQUISITION,
            "ACQUISITION_IN_PROGRESS",
        )

    if acquisition_job is not None and acquisition_job.status in {
        JobStatus.QUEUED.value,
        JobStatus.RUNNING.value,
    }:
        return (
            LibraryAssetAggregateStatus.IN_PROGRESS,
            LibraryAssetStage.ACQUISITION,
            "ACQUISITION_IN_PROGRESS",
        )

    if status.acquisition_status == "cancelled":
        return (
            LibraryAssetAggregateStatus.NEEDS_ACTION,
            LibraryAssetStage.ACQUISITION,
            "ACQUISITION_CANCELLED",
        )

    if status.stored or status.acquisition_status == "stored":
        return (
            LibraryAssetAggregateStatus.NEEDS_ACTION,
            LibraryAssetStage.PROCESSING,
            "PROCESSING_REQUIRED",
        )

    if status.acquisition_allowed is True:
        return (
            LibraryAssetAggregateStatus.NEEDS_ACTION,
            LibraryAssetStage.ACQUISITION,
            "ACQUISITION_READY",
        )

    if status.rights_state in {None, "unknown"}:
        return (
            LibraryAssetAggregateStatus.NEEDS_ACTION,
            LibraryAssetStage.RIGHTS,
            "RIGHTS_REVIEW_REQUIRED",
        )

    return (
        LibraryAssetAggregateStatus.NEEDS_ACTION,
        LibraryAssetStage.RIGHTS,
        "RIGHTS_BLOCKED",
    )


def _summary(items: list[LibraryAssetStatusItem]) -> LibraryStatusSummary:
    counts = {status: 0 for status in LibraryAssetAggregateStatus}
    for item in items:
        counts[item.aggregate_status] += 1
    return LibraryStatusSummary(
        total=len(items),
        ready=counts[LibraryAssetAggregateStatus.READY],
        in_progress=counts[LibraryAssetAggregateStatus.IN_PROGRESS],
        needs_action=counts[LibraryAssetAggregateStatus.NEEDS_ACTION],
        failed=counts[LibraryAssetAggregateStatus.FAILED],
    )


def _status_order(status: LibraryAssetAggregateStatus) -> int:
    return {
        LibraryAssetAggregateStatus.FAILED: 0,
        LibraryAssetAggregateStatus.NEEDS_ACTION: 1,
        LibraryAssetAggregateStatus.IN_PROGRESS: 2,
        LibraryAssetAggregateStatus.READY: 3,
    }[status]


__all__ = ["LibraryStatusService"]
