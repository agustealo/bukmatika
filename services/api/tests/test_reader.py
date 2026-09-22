from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.reader_models import Bookmark, ReadingState
from bukmatika.persistence.readers import ReaderAccessDenied, ReaderBookmarkNotFound
from bukmatika.reader import BookmarkCreate, ReaderService, ReadingProgressUpdate


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_reader_document(
    session: AsyncSession,
    *,
    suffix: str,
    edition_specific: bool = True,
) -> tuple[LibraryEntry, Document, list[DocumentSection]]:
    principal = Principal(kind="local", external_subject=f"reader-{suffix}")
    work = Work(
        canonical_title=f"Reader Work {suffix}",
        normalized_title=f"reader work {suffix}",
    )
    session.add_all([principal, work])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Reader Edition {suffix}",
        language="en",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/aa/bb/{suffix}",
        byte_size=128,
        media_type="text/plain",
    )
    session.add_all([edition, stored])
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/{suffix}.txt",
        stored_object_id=stored.id,
        byte_size=128,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id if edition_specific else None,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="1",
        section_count=3,
        chunk_count=3,
    )
    session.add(document)
    await session.flush()

    sections = [
        DocumentSection(
            document_id=document.id,
            ordinal=index,
            heading=f"Section {index + 1}",
            locator={"page": index + 1, "section": index + 1},
            text=f"Section {index + 1} reader text with durable coordinates.",
        )
        for index in range(3)
    ]
    session.add_all(sections)
    await session.flush()
    return entry, document, sections


async def test_reader_paginates_sections_and_persists_progress(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="page")
    service = ReaderService(session_scope_factory=_scope(session))

    first_page = await service.open_reader(
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=2,
    )
    assert [item.ordinal for item in first_page.sections] == [0, 1]
    assert first_page.next_after_ordinal == 1
    assert first_page.reading_state is None

    saved = await service.save_progress(
        library_entry_id=entry.id,
        document_id=document.id,
        update=ReadingProgressUpdate(
            section_id=sections[1].id,
            char_offset=7,
            progress_fraction=0.5,
        ),
    )
    assert saved.status == "reading"
    assert saved.locator == {"page": 2, "section": 2}

    session.expire_all()
    reopened = await service.open_reader(
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=1,
        limit=2,
    )
    assert [item.ordinal for item in reopened.sections] == [2]
    assert reopened.next_after_ordinal is None
    assert reopened.reading_state is not None
    assert reopened.reading_state.progress_fraction == 0.5
    assert reopened.reading_state.char_offset == 7


async def test_reader_rejects_document_outside_library_entry(session: AsyncSession) -> None:
    entry, _, _ = await _seed_reader_document(session, suffix="owned")
    _, other_document, _ = await _seed_reader_document(session, suffix="other")
    service = ReaderService(session_scope_factory=_scope(session))

    with pytest.raises(ReaderAccessDenied, match="does not own"):
        await service.open_reader(
            library_entry_id=entry.id,
            document_id=other_document.id,
            after_ordinal=None,
            limit=10,
        )


async def test_work_level_library_entry_can_read_owned_work_edition(
    session: AsyncSession,
) -> None:
    entry, document, _ = await _seed_reader_document(
        session,
        suffix="work-level",
        edition_specific=False,
    )
    service = ReaderService(session_scope_factory=_scope(session))

    response = await service.open_reader(
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=10,
    )
    assert response.document_id == document.id


async def test_progress_rejects_offset_outside_section(session: AsyncSession) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="offset")
    service = ReaderService(session_scope_factory=_scope(session))

    with pytest.raises(ValueError, match="outside the section text"):
        await service.save_progress(
            library_entry_id=entry.id,
            document_id=document.id,
            update=ReadingProgressUpdate(
                section_id=sections[0].id,
                char_offset=len(sections[0].text) + 1,
                progress_fraction=0.1,
            ),
        )


async def test_bookmark_is_idempotent_and_removable(session: AsyncSession) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="bookmark")
    service = ReaderService(session_scope_factory=_scope(session))
    create = BookmarkCreate(section_id=sections[0].id, char_offset=3, label="Return here")

    first = await service.add_bookmark(
        library_entry_id=entry.id,
        document_id=document.id,
        create=create,
    )
    second = await service.add_bookmark(
        library_entry_id=entry.id,
        document_id=document.id,
        create=create,
    )
    assert first.bookmark_id == second.bookmark_id
    assert first.locator == {"page": 1, "section": 1}

    state_count = len((await session.scalars(ReadingState.__table__.select())).all())
    bookmark_count = len((await session.scalars(Bookmark.__table__.select())).all())
    assert state_count == 1
    assert bookmark_count == 1

    await service.remove_bookmark(
        library_entry_id=entry.id,
        document_id=document.id,
        bookmark_id=first.bookmark_id,
    )
    with pytest.raises(ReaderBookmarkNotFound):
        await service.remove_bookmark(
            library_entry_id=entry.id,
            document_id=document.id,
            bookmark_id=first.bookmark_id,
        )
