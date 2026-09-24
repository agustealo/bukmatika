from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import LibraryService
from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.library import LibraryRepository
from bukmatika.persistence.library_resume import LibraryResumeRepository
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Principal, StoredObject, Work
from bukmatika.persistence.reader_models import ReadingState
from bukmatika.persistence.readers import ReaderRepository


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_two_document_work(
    session: AsyncSession,
) -> tuple[
    Principal,
    LibraryEntry,
    Document,
    DocumentSection,
    Document,
    DocumentSection,
]:
    principal = Principal(kind="local", external_subject="library-resume-annotation-recency")
    work = Work(
        canonical_title="Resume Authority Work",
        normalized_title="resume authority work",
    )
    stored_read = StoredObject(
        sha256="1" * 64,
        storage_key="objects/resume/read",
        byte_size=128,
        media_type="application/pdf",
    )
    stored_annotation = StoredObject(
        sha256="2" * 64,
        storage_key="objects/resume/annotation",
        byte_size=128,
        media_type="application/epub+zip",
    )
    session.add_all([principal, work, stored_read, stored_annotation])
    await session.flush()

    read_edition = Edition(
        work_id=work.id,
        title="Read Edition",
        language="en",
    )
    annotation_edition = Edition(
        work_id=work.id,
        title="Annotation Edition",
        language="en",
    )
    session.add_all([read_edition, annotation_edition])
    await session.flush()

    read_asset = Asset(
        edition_id=read_edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url="https://example.org/resume-read.pdf",
        stored_object_id=stored_read.id,
        byte_size=stored_read.byte_size,
    )
    annotation_asset = Asset(
        edition_id=annotation_edition.id,
        format="EPUB",
        media_type="application/epub+zip",
        remote_url="https://example.org/resume-annotation.epub",
        stored_object_id=stored_annotation.id,
        byte_size=stored_annotation.byte_size,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=None,
        status="saved",
    )
    session.add_all([read_asset, annotation_asset, entry])
    await session.flush()

    read_document = Document(
        asset_id=read_asset.id,
        stored_object_id=stored_read.id,
        source_sha256=stored_read.sha256,
        format="PDF",
        parser_name="pdf",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    annotation_document = Document(
        asset_id=annotation_asset.id,
        stored_object_id=stored_annotation.id,
        source_sha256=stored_annotation.sha256,
        format="EPUB",
        parser_name="epub",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add_all([read_document, annotation_document])
    await session.flush()

    read_section = DocumentSection(
        document_id=read_document.id,
        ordinal=0,
        heading="Read chapter",
        locator={"page": 1},
        text="The document that the reader actually opened and read.",
    )
    annotation_section = DocumentSection(
        document_id=annotation_document.id,
        ordinal=0,
        heading="Reference chapter",
        locator={"spine": 0},
        text="A second edition used only to save a reference bookmark.",
    )
    session.add_all([read_section, annotation_section])
    await session.flush()
    return (
        principal,
        entry,
        read_document,
        read_section,
        annotation_document,
        annotation_section,
    )


async def test_annotation_only_state_does_not_override_actual_library_resume(
    session: AsyncSession,
) -> None:
    (
        principal,
        entry,
        read_document,
        read_section,
        annotation_document,
        annotation_section,
    ) = await _seed_two_document_work(session)

    session.add(
        ReadingState(
            library_entry_id=entry.id,
            document_id=read_document.id,
            status="reading",
            progress_fraction=0.5,
            section_id=read_section.id,
            section_ordinal=read_section.ordinal,
            char_offset=10,
            locator=read_section.locator,
            last_read_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    await session.flush()

    reader_repository = ReaderRepository(session)
    annotation_access = await reader_repository.require_access(
        principal.id,
        entry.id,
        annotation_document.id,
    )
    await reader_repository.add_bookmark(
        access=annotation_access,
        section_id=annotation_section.id,
        char_offset=0,
        label="Reference only",
    )

    annotation_state = await session.scalar(
        select(ReadingState).where(
            ReadingState.library_entry_id == entry.id,
            ReadingState.document_id == annotation_document.id,
        )
    )
    assert annotation_state is not None
    assert annotation_state.status == "unread"
    assert annotation_state.progress_fraction == 0.0
    assert annotation_state.last_read_at is None

    library = await LibraryService(session_scope_factory=_scope(session)).list_library(
        principal_id=principal.id
    )
    assert len(library.items) == 1
    item = library.items[0]
    assert item.readable_document_id == read_document.id
    assert item.readable_format == "PDF"
    assert item.progress_fraction == 0.5
    assert item.reading_status == "reading"


async def test_annotation_only_state_is_not_a_resume_candidate(
    session: AsyncSession,
) -> None:
    (
        principal,
        entry,
        _read_document,
        _read_section,
        annotation_document,
        annotation_section,
    ) = await _seed_two_document_work(session)

    reader_repository = ReaderRepository(session)
    annotation_access = await reader_repository.require_access(
        principal.id,
        entry.id,
        annotation_document.id,
    )
    await reader_repository.add_bookmark(
        access=annotation_access,
        section_id=annotation_section.id,
        char_offset=0,
        label="Reference only",
    )

    resume = await LibraryResumeRepository(session).latest_for_entry(entry)
    assert resume is None

    fallback = await LibraryRepository(session).readable_document_for_entry(entry)
    assert fallback is not None
    fallback_document, fallback_asset = fallback

    library = await LibraryService(session_scope_factory=_scope(session)).list_library(
        principal_id=principal.id
    )
    assert len(library.items) == 1
    item = library.items[0]
    assert item.readable_document_id == fallback_document.id
    assert item.readable_format == fallback_asset.format
    assert item.progress_fraction is None
    assert item.reading_status is None
