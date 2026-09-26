from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import (
    Document,
    DocumentChunk,
    DocumentProcessingState,
    DocumentSection,
)
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


@dataclass(frozen=True, slots=True)
class SeededAsset:
    title: str
    work_id: UUID
    library_entry_id: UUID
    asset_id: UUID
    document_id: UUID | None = None


def _digest(principal_id: UUID, suffix: str) -> str:
    return hashlib.sha256(f"browser-status:{principal_id}:{suffix}".encode()).hexdigest()


async def _owned_asset(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    suffix: str,
    rights_state: str = "public_domain",
    acquisition_allowed: bool = True,
    stored: bool = False,
) -> tuple[SeededAsset, StoredObject | None]:
    title = f"Status Center {suffix.title()} {principal_id.hex[:8]}"
    work = Work(canonical_title=title, normalized_title=title.casefold())
    database_session.add(work)
    await database_session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"{title} Edition",
        language="en",
        publication_year=1901,
        publisher="Bukmatika browser proof",
        edition_statement="Deterministic Status Center fixture",
    )
    database_session.add(edition)
    await database_session.flush()

    stored_object: StoredObject | None = None
    if stored:
        stored_object = StoredObject(
            sha256=_digest(principal_id, suffix),
            storage_key=f"browser-status/{principal_id}/{suffix}.pdf",
            byte_size=128,
            media_type="application/pdf",
        )
        database_session.add(stored_object)
        await database_session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.invalid/browser-status/{principal_id}/{suffix}.pdf",
        stored_object_id=stored_object.id if stored_object is not None else None,
        byte_size=128,
    )
    entry = LibraryEntry(
        principal_id=principal_id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    database_session.add_all((asset, entry))
    await database_session.flush()

    database_session.add(
        RightsDecision(
            subject_type="asset",
            subject_id=asset.id,
            rights_state=rights_state,
            jurisdiction="US",
            policy_version="browser-status-v1",
            permissions={
                "download": acquisition_allowed,
                "process": acquisition_allowed,
                "ocr": acquisition_allowed,
            },
            reason="Deterministic Status Center browser fixture.",
        )
    )
    await database_session.flush()
    return (
        SeededAsset(
            title=title,
            work_id=work.id,
            library_entry_id=entry.id,
            asset_id=asset.id,
        ),
        stored_object,
    )


async def seed(database_session: AsyncSession, principal_id: UUID) -> dict[str, object]:
    blocked, _ = await _owned_asset(
        database_session,
        principal_id=principal_id,
        suffix="blocked",
        rights_state="restricted",
        acquisition_allowed=False,
    )

    queued, _ = await _owned_asset(
        database_session,
        principal_id=principal_id,
        suffix="queued",
    )
    queued_acquisition = Acquisition(
        asset_id=queued.asset_id,
        status="queued",
        remote_url=f"https://example.invalid/browser-status/{principal_id}/queued.pdf",
        expected_format="PDF",
    )
    database_session.add(queued_acquisition)
    await database_session.flush()
    database_session.add(
        Job(
            job_type="acquisition",
            payload={
                "asset_id": str(queued.asset_id),
                "acquisition_id": str(queued_acquisition.id),
            },
            dedupe_key=f"browser-status-acquisition:{queued_acquisition.id}",
            status="queued",
            attempt_count=0,
            max_attempts=3,
        )
    )

    stored, stored_object = await _owned_asset(
        database_session,
        principal_id=principal_id,
        suffix="stored",
        stored=True,
    )
    assert stored_object is not None
    database_session.add(
        Acquisition(
            asset_id=stored.asset_id,
            status="stored",
            remote_url=f"https://example.invalid/browser-status/{principal_id}/stored.pdf",
            expected_format="PDF",
            stored_object_id=stored_object.id,
        )
    )

    ocr, ocr_object = await _owned_asset(
        database_session,
        principal_id=principal_id,
        suffix="ocr",
        stored=True,
    )
    assert ocr_object is not None
    database_session.add_all(
        (
            Acquisition(
                asset_id=ocr.asset_id,
                status="stored",
                remote_url=f"https://example.invalid/browser-status/{principal_id}/ocr.pdf",
                expected_format="PDF",
                stored_object_id=ocr_object.id,
            ),
            DocumentProcessingState(
                asset_id=ocr.asset_id,
                stored_object_id=ocr_object.id,
                source_sha256=ocr_object.sha256,
                format="PDF",
                processor_name="browser-status",
                processor_version="1",
                status="requires_ocr",
            ),
            Job(
                job_type="document_ocr",
                payload={
                    "asset_id": str(ocr.asset_id),
                    "stored_object_id": str(ocr_object.id),
                    "source_sha256": ocr_object.sha256,
                },
                dedupe_key=f"browser-status-ocr:{ocr.asset_id}",
                status="running",
                attempt_count=1,
                max_attempts=2,
            ),
        )
    )

    failed, _ = await _owned_asset(
        database_session,
        principal_id=principal_id,
        suffix="failed",
    )
    database_session.add(
        Acquisition(
            asset_id=failed.asset_id,
            status="failed",
            remote_url=f"https://example.invalid/browser-status/{principal_id}/failed.pdf",
            expected_format="PDF",
            error_code="REMOTE_DOWNLOAD_FAILED",
        )
    )

    ready, ready_object = await _owned_asset(
        database_session,
        principal_id=principal_id,
        suffix="ready",
        stored=True,
    )
    assert ready_object is not None
    database_session.add_all(
        (
            Acquisition(
                asset_id=ready.asset_id,
                status="stored",
                remote_url=f"https://example.invalid/browser-status/{principal_id}/ready.pdf",
                expected_format="PDF",
                stored_object_id=ready_object.id,
            ),
            DocumentProcessingState(
                asset_id=ready.asset_id,
                stored_object_id=ready_object.id,
                source_sha256=ready_object.sha256,
                format="PDF",
                processor_name="browser-status",
                processor_version="1",
                status="completed",
            ),
        )
    )
    await database_session.flush()

    ready_document = Document(
        asset_id=ready.asset_id,
        stored_object_id=ready_object.id,
        source_sha256=ready_object.sha256,
        format="PDF",
        parser_name="browser-status",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    database_session.add(ready_document)
    await database_session.flush()

    ready_text = "The Status Center ready-state handoff opens canonical processed reader evidence."
    ready_heading = "Ready pipeline evidence"
    ready_section = DocumentSection(
        document_id=ready_document.id,
        ordinal=0,
        heading=ready_heading,
        locator={"page": 1},
        text=ready_text,
    )
    database_session.add(ready_section)
    await database_session.flush()
    database_session.add(
        DocumentChunk(
            document_id=ready_document.id,
            section_id=ready_section.id,
            ordinal=0,
            char_start=0,
            char_end=len(ready_text),
            text=ready_text,
        )
    )

    foreign = Principal(
        kind="local",
        external_subject=f"browser-status-foreign-{principal_id}",
    )
    database_session.add(foreign)
    await database_session.flush()
    foreign_asset, _ = await _owned_asset(
        database_session,
        principal_id=foreign.id,
        suffix="foreign",
        rights_state="restricted",
        acquisition_allowed=False,
    )

    await database_session.flush()
    return {
        "titles": {
            "blocked": blocked.title,
            "queued": queued.title,
            "stored": stored.title,
            "ocr": ocr.title,
            "failed": failed.title,
            "ready": ready.title,
            "foreign": foreign_asset.title,
        },
        "blocked_work_id": str(blocked.work_id),
        "ready_library_entry_id": str(ready.library_entry_id),
        "ready_document_id": str(ready_document.id),
        "ready_heading": ready_heading,
    }


async def run(principal_id: UUID) -> dict[str, object]:
    async with session_scope() as database_session:
        return await seed(database_session, principal_id)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_status_seed.py <principal-id>")
    principal_id = UUID(sys.argv[1])
    print(json.dumps(asyncio.run(run(principal_id)), sort_keys=True))


if __name__ == "__main__":
    main()
