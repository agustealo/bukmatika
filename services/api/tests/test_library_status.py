from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import LibraryAssetAggregateStatus, LibraryAssetStage
from bukmatika.library.status import LibraryStatusService
from bukmatika.main import app
from bukmatika.persistence.document_models import Document, DocumentProcessingState
from bukmatika.persistence.jobs import Job
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    RightsDecision,
    StoredObject,
    Work,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _owned_asset(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    rights_state: str = "public_domain",
    acquisition_allowed: bool = True,
    stored: bool = False,
) -> tuple[Work, Edition, Asset, LibraryEntry, StoredObject | None]:
    work = Work(
        canonical_title=f"Status Work {suffix}",
        normalized_title=f"status work {suffix}",
    )
    session.add(work)
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Status Edition {suffix}",
        language="en",
    )
    session.add(edition)
    await session.flush()

    stored_object: StoredObject | None = None
    if stored:
        stored_object = StoredObject(
            sha256=(suffix.encode().hex() + "0" * 64)[:64],
            storage_key=f"objects/status/{suffix}",
            byte_size=64,
            media_type="application/pdf",
        )
        session.add(stored_object)
        await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.org/{suffix}.pdf",
        stored_object_id=stored_object.id if stored_object is not None else None,
        byte_size=64,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()
    session.add(
        RightsDecision(
            subject_type="asset",
            subject_id=asset.id,
            rights_state=rights_state,
            jurisdiction="US",
            policy_version="status-test-v1",
            permissions={
                "download": acquisition_allowed,
                "process": acquisition_allowed,
                "ocr": acquisition_allowed,
            },
            reason="Status center fixture rights decision.",
        )
    )
    await session.flush()
    return work, edition, asset, entry, stored_object


async def test_status_center_projects_canonical_pipeline_states_and_summary(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="status-owner")
    session.add(principal)
    await session.flush()

    _, _, blocked_asset, _, _ = await _owned_asset(
        session,
        principal=principal,
        suffix="blocked",
        rights_state="restricted",
        acquisition_allowed=False,
    )

    _, _, queued_asset, _, _ = await _owned_asset(
        session,
        principal=principal,
        suffix="queued",
    )
    queued_acquisition = Acquisition(
        asset_id=queued_asset.id,
        status="queued",
        remote_url=queued_asset.remote_url or "",
        expected_format="PDF",
    )
    session.add(queued_acquisition)
    await session.flush()
    session.add(
        Job(
            job_type="acquisition",
            payload={
                "asset_id": str(queued_asset.id),
                "acquisition_id": str(queued_acquisition.id),
            },
            dedupe_key=f"acquisition:{queued_acquisition.id}",
            status="queued",
            attempt_count=0,
            max_attempts=3,
        )
    )

    _, _, stored_asset, _, stored_object = await _owned_asset(
        session,
        principal=principal,
        suffix="stored",
        stored=True,
    )
    assert stored_object is not None
    session.add(
        Acquisition(
            asset_id=stored_asset.id,
            status="stored",
            remote_url=stored_asset.remote_url or "",
            expected_format="PDF",
            stored_object_id=stored_object.id,
        )
    )

    _, _, ocr_asset, _, ocr_object = await _owned_asset(
        session,
        principal=principal,
        suffix="ocr",
        stored=True,
    )
    assert ocr_object is not None
    session.add_all(
        [
            Acquisition(
                asset_id=ocr_asset.id,
                status="stored",
                remote_url=ocr_asset.remote_url or "",
                expected_format="PDF",
                stored_object_id=ocr_object.id,
            ),
            DocumentProcessingState(
                asset_id=ocr_asset.id,
                stored_object_id=ocr_object.id,
                source_sha256=ocr_object.sha256,
                format="PDF",
                processor_name="pdfium",
                processor_version="1",
                status="requires_ocr",
            ),
            Job(
                job_type="document_ocr",
                payload={
                    "asset_id": str(ocr_asset.id),
                    "stored_object_id": str(ocr_object.id),
                    "source_sha256": ocr_object.sha256,
                },
                dedupe_key=f"ocr:{ocr_asset.id}",
                status="running",
                attempt_count=1,
                max_attempts=2,
            ),
        ]
    )

    _, _, failed_asset, _, _ = await _owned_asset(
        session,
        principal=principal,
        suffix="failed",
    )
    session.add(
        Acquisition(
            asset_id=failed_asset.id,
            status="failed",
            remote_url=failed_asset.remote_url or "",
            expected_format="PDF",
            error_code="REMOTE_DOWNLOAD_FAILED",
        )
    )

    _, _, ready_asset, _, ready_object = await _owned_asset(
        session,
        principal=principal,
        suffix="ready",
        stored=True,
    )
    assert ready_object is not None
    session.add_all(
        [
            Acquisition(
                asset_id=ready_asset.id,
                status="stored",
                remote_url=ready_asset.remote_url or "",
                expected_format="PDF",
                stored_object_id=ready_object.id,
            ),
            DocumentProcessingState(
                asset_id=ready_asset.id,
                stored_object_id=ready_object.id,
                source_sha256=ready_object.sha256,
                format="PDF",
                processor_name="pdfium",
                processor_version="1",
                status="completed",
            ),
        ]
    )
    await session.flush()
    ready_document = Document(
        asset_id=ready_asset.id,
        stored_object_id=ready_object.id,
        source_sha256=ready_object.sha256,
        format="PDF",
        parser_name="pdfium",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(ready_document)
    await session.flush()

    response = await LibraryStatusService(session_scope_factory=_scope(session)).snapshot(
        principal_id=principal.id
    )
    by_asset = {item.asset_id: item for item in response.items}

    assert by_asset[blocked_asset.id].aggregate_status is LibraryAssetAggregateStatus.NEEDS_ACTION
    assert by_asset[blocked_asset.id].stage is LibraryAssetStage.RIGHTS
    assert by_asset[blocked_asset.id].status_code == "RIGHTS_BLOCKED"

    assert by_asset[queued_asset.id].aggregate_status is LibraryAssetAggregateStatus.IN_PROGRESS
    assert by_asset[queued_asset.id].stage is LibraryAssetStage.ACQUISITION
    assert by_asset[queued_asset.id].acquisition_job_status == "queued"

    assert by_asset[stored_asset.id].aggregate_status is LibraryAssetAggregateStatus.NEEDS_ACTION
    assert by_asset[stored_asset.id].stage is LibraryAssetStage.PROCESSING
    assert by_asset[stored_asset.id].status_code == "PROCESSING_REQUIRED"

    assert by_asset[ocr_asset.id].aggregate_status is LibraryAssetAggregateStatus.IN_PROGRESS
    assert by_asset[ocr_asset.id].stage is LibraryAssetStage.OCR
    assert by_asset[ocr_asset.id].ocr_job_status == "running"

    assert by_asset[failed_asset.id].aggregate_status is LibraryAssetAggregateStatus.FAILED
    assert by_asset[failed_asset.id].status_code == "REMOTE_DOWNLOAD_FAILED"

    assert by_asset[ready_asset.id].aggregate_status is LibraryAssetAggregateStatus.READY
    assert by_asset[ready_asset.id].document_id == ready_document.id

    assert response.summary.total == 6
    assert response.summary.ready == 1
    assert response.summary.in_progress == 2
    assert response.summary.needs_action == 2
    assert response.summary.failed == 1


async def test_status_center_is_principal_scoped_and_deduplicates_overlapping_ownership(
    session: AsyncSession,
) -> None:
    owner = Principal(kind="local", external_subject="status-dedupe-owner")
    other = Principal(kind="local", external_subject="status-dedupe-other")
    session.add_all([owner, other])
    await session.flush()

    work, edition, asset, edition_entry, _ = await _owned_asset(
        session,
        principal=owner,
        suffix="overlap",
    )
    work_entry = LibraryEntry(
        principal_id=owner.id,
        work_id=work.id,
        edition_id=None,
        status="saved",
    )
    session.add(work_entry)
    _, _, foreign_asset, _, _ = await _owned_asset(
        session,
        principal=other,
        suffix="foreign",
    )
    await session.flush()

    response = await LibraryStatusService(session_scope_factory=_scope(session)).snapshot(
        principal_id=owner.id
    )

    assert len(response.items) == 1
    assert response.items[0].asset_id == asset.id
    assert response.items[0].edition_id == edition.id
    assert response.items[0].library_entry_id == edition_entry.id
    assert all(item.asset_id != foreign_asset.id for item in response.items)


def test_library_status_route_is_mounted() -> None:
    paths = app.openapi()["paths"]
    assert "/v1/library/status" in paths
    assert "get" in paths["/v1/library/status"]
