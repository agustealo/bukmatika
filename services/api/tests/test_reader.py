from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.events import SemanticEventType
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.reader_models import Bookmark, Highlight, ReadingState
from bukmatika.persistence.readers import (
    ReaderAccessDenied,
    ReaderBookmarkNotFound,
    ReaderHighlightNotFound,
    ReaderPositionInvalid,
)
from bukmatika.reader import (
    BookmarkCreate,
    HighlightCreate,
    HighlightNoteUpdate,
    ReaderService,
    ReadingProgressUpdate,
)


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
    principal_id = entry.principal_id
    entry_id = entry.id
    document_id = document.id
    service = ReaderService(session_scope_factory=_scope(session))

    first_page = await service.open_reader(
        principal_id=principal_id,
        library_entry_id=entry_id,
        document_id=document_id,
        after_ordinal=None,
        limit=2,
    )
    assert [item.ordinal for item in first_page.sections] == [0, 1]
    assert first_page.next_after_ordinal == 1
    assert first_page.reading_state is None
    assert first_page.highlights == []

    saved = await service.save_progress(
        principal_id=principal_id,
        library_entry_id=entry_id,
        document_id=document_id,
        update=ReadingProgressUpdate(
            section_id=sections[1].id,
            char_offset=7,
        ),
    )
    expected_progress = (sections[1].ordinal + 7 / len(sections[1].text)) / document.section_count
    assert saved.status == "reading"
    assert saved.progress_fraction == pytest.approx(expected_progress)
    assert saved.locator == {"page": 2, "section": 2}

    session.expire_all()
    reopened = await service.open_reader(
        principal_id=principal_id,
        library_entry_id=entry_id,
        document_id=document_id,
        after_ordinal=1,
        limit=2,
    )
    assert [item.ordinal for item in reopened.sections] == [2]
    assert reopened.next_after_ordinal is None
    assert reopened.reading_state is not None
    assert reopened.reading_state.progress_fraction == pytest.approx(expected_progress)
    assert reopened.reading_state.char_offset == 7


async def test_reader_progress_ignores_legacy_client_fraction_and_does_not_finish_at_last_section_start(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="progress-authority")
    service = ReaderService(session_scope_factory=_scope(session))
    final_section = sections[-1]
    update = ReadingProgressUpdate.model_validate(
        {
            "section_id": final_section.id,
            "char_offset": 0,
            "progress_fraction": 1.0,
        }
    )

    saved = await service.save_progress(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        update=update,
    )

    assert saved.status == "reading"
    assert saved.progress_fraction == pytest.approx(final_section.ordinal / document.section_count)
    assert saved.progress_fraction < 1.0


async def test_reader_finishes_only_at_document_end_and_position_updates_remain_reversible(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="completion")
    service = ReaderService(session_scope_factory=_scope(session))
    final_section = sections[-1]

    finished = await service.save_progress(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        update=ReadingProgressUpdate(
            section_id=final_section.id,
            char_offset=len(final_section.text),
        ),
    )
    assert finished.status == "finished"
    assert finished.progress_fraction == 1.0

    moved_back = await service.save_progress(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        update=ReadingProgressUpdate(section_id=sections[0].id, char_offset=0),
    )
    assert moved_back.status == "reading"
    assert moved_back.progress_fraction == 0.0
    assert moved_back.section_id == sections[0].id
    assert moved_back.char_offset == 0


async def test_annotation_state_does_not_claim_reading_progress(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="annotation-state")
    service = ReaderService(session_scope_factory=_scope(session))
    final_section = sections[-1]

    await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=final_section.id,
            char_offset=len(final_section.text),
            label="Reference only",
        ),
    )

    state = await session.scalar(
        select(ReadingState).where(
            ReadingState.library_entry_id == entry.id,
            ReadingState.document_id == document.id,
        )
    )
    assert state is not None
    assert state.status == "unread"
    assert state.progress_fraction == 0.0


async def test_reader_rejects_document_outside_library_entry(session: AsyncSession) -> None:
    entry, _, _ = await _seed_reader_document(session, suffix="owned")
    _, other_document, _ = await _seed_reader_document(session, suffix="other")
    service = ReaderService(session_scope_factory=_scope(session))

    with pytest.raises(ReaderAccessDenied, match="does not own"):
        await service.open_reader(
            principal_id=entry.principal_id,
            library_entry_id=entry.id,
            document_id=other_document.id,
            after_ordinal=None,
            limit=10,
        )


async def test_reader_rejects_another_principal_with_exact_ids(session: AsyncSession) -> None:
    owned_entry, owned_document, _ = await _seed_reader_document(session, suffix="owner")
    intruder_entry, _, _ = await _seed_reader_document(session, suffix="intruder")
    service = ReaderService(session_scope_factory=_scope(session))

    with pytest.raises(ReaderAccessDenied, match="does not own"):
        await service.open_reader(
            principal_id=intruder_entry.principal_id,
            library_entry_id=owned_entry.id,
            document_id=owned_document.id,
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
        principal_id=entry.principal_id,
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
            principal_id=entry.principal_id,
            library_entry_id=entry.id,
            document_id=document.id,
            update=ReadingProgressUpdate(
                section_id=sections[0].id,
                char_offset=len(sections[0].text) + 1,
            ),
        )


async def test_bookmark_is_idempotent_and_removable(session: AsyncSession) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="bookmark")
    service = ReaderService(session_scope_factory=_scope(session))
    create = BookmarkCreate(section_id=sections[0].id, char_offset=3, label="Return here")

    first = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=create,
    )
    second = await service.add_bookmark(
        principal_id=entry.principal_id,
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
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        bookmark_id=first.bookmark_id,
    )
    with pytest.raises(ReaderBookmarkNotFound):
        await service.remove_bookmark(
            principal_id=entry.principal_id,
            library_entry_id=entry.id,
            document_id=document.id,
            bookmark_id=first.bookmark_id,
        )


async def test_highlight_is_coordinate_derived_idempotent_and_restart_safe(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="highlight")
    service = ReaderService(session_scope_factory=_scope(session))
    section = sections[0]
    principal_id = entry.principal_id
    entry_id = entry.id
    document_id = document.id
    section_id = section.id
    char_start = 10
    char_end = 28
    expected_text = section.text[char_start:char_end]
    expected_locator = dict(section.locator)

    first = await service.add_highlight(
        principal_id=principal_id,
        library_entry_id=entry_id,
        document_id=document_id,
        create=HighlightCreate(
            section_id=section_id,
            char_start=char_start,
            char_end=char_end,
            note="  Compare this claim later.  ",
        ),
    )
    second = await service.add_highlight(
        principal_id=principal_id,
        library_entry_id=entry_id,
        document_id=document_id,
        create=HighlightCreate(
            section_id=section_id,
            char_start=char_start,
            char_end=char_end,
            note="Revised note",
        ),
    )

    assert second.highlight_id == first.highlight_id
    assert second.text == expected_text
    assert second.note == "Revised note"
    assert second.locator == expected_locator
    assert "text" not in Highlight.__table__.columns
    assert len((await session.scalars(Highlight.__table__.select())).all()) == 1

    session.expire_all()
    reopened = await service.open_reader(
        principal_id=principal_id,
        library_entry_id=entry_id,
        document_id=document_id,
        after_ordinal=None,
        limit=10,
    )
    assert len(reopened.highlights) == 1
    assert reopened.highlights[0].highlight_id == first.highlight_id
    assert reopened.highlights[0].text == expected_text
    assert reopened.highlights[0].note == "Revised note"

    event = await session.scalar(
        select(InteractionEvent)
        .where(
            InteractionEvent.principal_id == principal_id,
            InteractionEvent.event_type == SemanticEventType.HIGHLIGHT_ADDED.value,
        )
        .order_by(InteractionEvent.occurred_at.desc(), InteractionEvent.id.desc())
        .limit(1)
    )
    assert event is not None
    assert "text" not in event.context
    assert "note" not in event.context
    assert event.context["char_start"] == char_start
    assert event.context["char_end"] == char_end


async def test_highlight_note_can_be_cleared_and_highlight_removed(session: AsyncSession) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="highlight-edit")
    service = ReaderService(session_scope_factory=_scope(session))
    created = await service.add_highlight(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=HighlightCreate(
            section_id=sections[1].id,
            char_start=0,
            char_end=12,
            note="Initial note",
        ),
    )

    updated = await service.update_highlight_note(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        highlight_id=created.highlight_id,
        update=HighlightNoteUpdate(note="   "),
    )
    assert updated.note is None
    assert updated.text == sections[1].text[:12]

    await service.remove_highlight(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        highlight_id=created.highlight_id,
    )
    with pytest.raises(ReaderHighlightNotFound):
        await service.remove_highlight(
            principal_id=entry.principal_id,
            library_entry_id=entry.id,
            document_id=document.id,
            highlight_id=created.highlight_id,
        )


async def test_highlight_rejects_invalid_range_and_cross_principal_write(
    session: AsyncSession,
) -> None:
    owned_entry, owned_document, sections = await _seed_reader_document(
        session,
        suffix="highlight-owned",
    )
    intruder_entry, _, _ = await _seed_reader_document(session, suffix="highlight-intruder")
    service = ReaderService(session_scope_factory=_scope(session))

    with pytest.raises(ReaderPositionInvalid, match="outside the section text"):
        await service.add_highlight(
            principal_id=owned_entry.principal_id,
            library_entry_id=owned_entry.id,
            document_id=owned_document.id,
            create=HighlightCreate(
                section_id=sections[0].id,
                char_start=1,
                char_end=len(sections[0].text) + 1,
            ),
        )

    with pytest.raises(ReaderAccessDenied, match="does not own"):
        await service.add_highlight(
            principal_id=intruder_entry.principal_id,
            library_entry_id=owned_entry.id,
            document_id=owned_document.id,
            create=HighlightCreate(
                section_id=sections[0].id,
                char_start=1,
                char_end=8,
            ),
        )
