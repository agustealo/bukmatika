from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import LibraryService
from bukmatika.persistence.document_models import Document, DocumentProcessingState, DocumentSection
from bukmatika.persistence.jobs import Job
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Contributor,
    Edition,
    LibraryEntry,
    Principal,
    RightsDecision,
    RightsEvidenceRecord,
    RightsEvidenceSubject,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)
from bukmatika.persistence.reader_models import ReadingState


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_dossier(
    session: AsyncSession,
    *,
    suffix: str,
) -> tuple[Principal, Work, Edition, Asset, Document, SourceRecord]:
    principal = Principal(kind="local", external_subject=f"library-{suffix}")
    work = Work(canonical_title=f"Work {suffix}", normalized_title=f"work {suffix}")
    contributor = Contributor(
        display_name=f"Author {suffix}",
        normalized_name=f"author {suffix}",
    )
    subject = Subject(display_name="History", normalized_name=f"history-{suffix}")
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/library/{suffix}",
        byte_size=256,
        media_type="application/pdf",
    )
    session.add_all([principal, work, contributor, subject, stored])
    await session.flush()

    session.add_all(
        [
            WorkContributor(
                work_id=work.id,
                contributor_id=contributor.id,
                role="author",
            ),
            WorkSubject(work_id=work.id, subject_id=subject.id),
        ]
    )
    edition = Edition(
        work_id=work.id,
        title=f"Edition {suffix}",
        language="en",
        publication_year=1898,
        publisher="Archive Press",
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.org/{suffix}.pdf",
        stored_object_id=stored.id,
        byte_size=256,
    )
    source = SourceRecord(
        provider="test_provider",
        provider_record_id=f"record-{suffix}",
        canonical_url=f"https://example.org/source/{suffix}",
    )
    session.add_all([asset, source])
    await session.flush()
    session.add(
        SourceRecordLink(
            source_record_id=source.id,
            entity_type="edition",
            entity_id=edition.id,
            relationship="describes",
        )
    )

    evidence = RightsEvidenceRecord(
        state="public_domain",
        source="test_provider",
        basis="Provider marks this exact asset public domain.",
        confidence=0.99,
    )
    session.add(evidence)
    await session.flush()
    session.add(
        RightsEvidenceSubject(
            rights_evidence_id=evidence.id,
            subject_type="asset",
            subject_id=asset.id,
        )
    )
    decision = RightsDecision(
        subject_type="asset",
        subject_id=asset.id,
        rights_state="public_domain",
        jurisdiction="US",
        policy_version="test-v1",
        permissions={"download": True, "process": True, "ocr": True},
        reason="Test evidence permits acquisition.",
    )
    acquisition = Acquisition(
        asset_id=asset.id,
        status="stored",
        remote_url=asset.remote_url or "",
        expected_format="PDF",
        stored_object_id=stored.id,
    )
    processing = DocumentProcessingState(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="PDF",
        processor_name="tesseract",
        processor_version="1",
        status="completed",
    )
    session.add_all([decision, acquisition, processing])
    await session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="PDF",
        parser_name="tesseract",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    ocr_job = Job(
        job_type="document_ocr",
        payload={
            "asset_id": str(asset.id),
            "stored_object_id": str(stored.id),
            "source_sha256": stored.sha256,
        },
        dedupe_key=f"ocr-{suffix}",
        status="completed",
        attempt_count=1,
        max_attempts=2,
    )
    session.add_all([document, ocr_job])
    await session.flush()
    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading="Page 1",
        locator={"page": 1},
        text="A canonical readable page.",
    )
    session.add(section)
    await session.flush()
    return principal, work, edition, asset, document, source


async def test_source_dossier_resolves_canonical_status_and_saved_edition(
    session: AsyncSession,
) -> None:
    principal, work, edition, asset, document, source = await _seed_dossier(
        session,
        suffix="dossier",
    )
    service = LibraryService(session_scope_factory=_scope(session))

    saved = await service.save_edition(principal_id=principal.id, edition_id=edition.id)
    response = await service.dossier_for_source(
        principal_id=principal.id,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
    )

    assert response.work_id == work.id
    assert response.authors == ["Author dossier"]
    assert response.subjects == ["History"]
    assert len(response.editions) == 1
    dossier_edition = response.editions[0]
    assert dossier_edition.library_entry_id == saved.library_entry_id
    assert len(dossier_edition.assets) == 1
    status = dossier_edition.assets[0]
    assert status.asset_id == asset.id
    assert status.acquisition_status == "stored"
    assert status.processing_status == "completed"
    assert status.ocr_job_status == "completed"
    assert status.document_id == document.id
    assert status.rights_state == "public_domain"
    assert status.acquisition_allowed is True


async def test_dossier_fallback_rights_uses_canonical_deny_priority(
    session: AsyncSession,
) -> None:
    principal, _, _, asset, _, source = await _seed_dossier(session, suffix="rights-fallback")
    await session.execute(
        delete(RightsDecision).where(
            RightsDecision.subject_type == "asset",
            RightsDecision.subject_id == asset.id,
        )
    )
    restricted = RightsEvidenceRecord(
        state="restricted",
        source="rights-review",
        basis="Exact asset is restricted despite permissive provider metadata.",
        confidence=0.1,
    )
    session.add(restricted)
    await session.flush()
    session.add(
        RightsEvidenceSubject(
            rights_evidence_id=restricted.id,
            subject_type="asset",
            subject_id=asset.id,
        )
    )
    await session.flush()

    response = await LibraryService(session_scope_factory=_scope(session)).dossier_for_source(
        principal_id=principal.id,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
    )

    status = response.editions[0].assets[0]
    assert status.rights_state == "restricted"
    assert status.acquisition_allowed is False


async def test_save_edition_is_idempotent_and_library_resolves_read_progress(
    session: AsyncSession,
) -> None:
    principal, _, edition, _, document, _ = await _seed_dossier(session, suffix="save")
    service = LibraryService(session_scope_factory=_scope(session))

    first = await service.save_edition(principal_id=principal.id, edition_id=edition.id)
    second = await service.save_edition(principal_id=principal.id, edition_id=edition.id)
    assert first.library_entry_id == second.library_entry_id

    state = ReadingState(
        library_entry_id=first.library_entry_id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.4,
        section_ordinal=0,
        char_offset=4,
        locator={"page": 1},
    )
    session.add(state)
    await session.flush()

    library = await service.list_library(principal_id=principal.id)
    assert len(library.items) == 1
    item = library.items[0]
    assert item.library_entry_id == first.library_entry_id
    assert item.readable_document_id == document.id
    assert item.readable_format == "PDF"
    assert item.progress_fraction == 0.4
    assert item.reading_status == "reading"

    count = len((await session.scalars(LibraryEntry.__table__.select())).all())
    assert count == 1


async def test_dossier_never_leaks_another_principals_library_state(
    session: AsyncSession,
) -> None:
    owner, _, edition, _, _, source = await _seed_dossier(session, suffix="isolation")
    intruder = Principal(kind="local", external_subject="library-intruder")
    session.add(intruder)
    await session.flush()
    service = LibraryService(session_scope_factory=_scope(session))

    owner_item = await service.save_edition(principal_id=owner.id, edition_id=edition.id)
    owner_dossier = await service.dossier_for_source(
        principal_id=owner.id,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
    )
    intruder_dossier = await service.dossier_for_source(
        principal_id=intruder.id,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
    )

    assert owner_dossier.editions[0].library_entry_id == owner_item.library_entry_id
    assert intruder_dossier.editions[0].library_entry_id is None
    assert (await service.list_library(principal_id=intruder.id)).items == []


async def test_work_level_save_applies_to_owned_work_editions(session: AsyncSession) -> None:
    principal, work, _, _, _, source = await _seed_dossier(session, suffix="work-save")
    service = LibraryService(session_scope_factory=_scope(session))

    saved = await service.save_work(principal_id=principal.id, work_id=work.id)
    dossier = await service.dossier_for_source(
        principal_id=principal.id,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
    )

    assert dossier.work_library_entry_id == saved.library_entry_id
    assert dossier.editions[0].library_entry_id == saved.library_entry_id
