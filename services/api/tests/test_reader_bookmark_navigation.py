from sqlalchemy.ext.asyncio import AsyncSession
from test_reader import _scope, _seed_reader_document

from bukmatika.reader import BookmarkCreate, ReaderService


async def test_bookmark_response_keeps_canonical_position_outside_loaded_page(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(
        session,
        suffix="bookmark-navigation",
    )
    service = ReaderService(session_scope_factory=_scope(session))

    created = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=sections[2].id,
            char_offset=3,
            label="Return here",
        ),
    )

    assert created.section_ordinal == 2
    assert created.char_offset == 3

    session.expire_all()
    reopened = await service.open_reader(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=1,
    )

    assert [section.ordinal for section in reopened.sections] == [0]
    assert len(reopened.bookmarks) == 1
    bookmark = reopened.bookmarks[0]
    assert bookmark.bookmark_id == created.bookmark_id
    assert bookmark.section_id == sections[2].id
    assert bookmark.section_ordinal == 2
    assert bookmark.char_offset == 3
