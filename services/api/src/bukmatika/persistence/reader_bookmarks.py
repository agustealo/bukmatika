from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import DocumentSection
from bukmatika.persistence.reader_models import Bookmark, ReadingState
from bukmatika.persistence.readers import (
    ReaderAccess,
    ReaderBookmarkNotFound,
    ReaderBookmarkRecord,
)


class ReaderBookmarkConflict(RuntimeError):
    """The bookmark changed after the caller loaded its revision."""


async def remove_bookmark_if_current(
    session: AsyncSession,
    *,
    access: ReaderAccess,
    bookmark_id: UUID,
    expected_updated_at: datetime | None,
) -> None:
    record = await _locked_bookmark_record(
        session,
        access=access,
        bookmark_id=bookmark_id,
    )
    if (
        expected_updated_at is not None
        and record.bookmark.updated_at != expected_updated_at
    ):
        raise ReaderBookmarkConflict("Bookmark changed after this removal started")

    await session.delete(record.bookmark)
    await session.flush()


async def _locked_bookmark_record(
    session: AsyncSession,
    *,
    access: ReaderAccess,
    bookmark_id: UUID,
) -> ReaderBookmarkRecord:
    state_id = await session.scalar(
        select(ReadingState.id).where(
            ReadingState.library_entry_id == access.library_entry_id,
            ReadingState.document_id == access.document.id,
        )
    )
    if state_id is None:
        raise ReaderBookmarkNotFound("Bookmark does not exist")

    row = (
        await session.execute(
            select(Bookmark, DocumentSection)
            .join(DocumentSection, DocumentSection.id == Bookmark.section_id)
            .where(
                Bookmark.id == bookmark_id,
                Bookmark.reading_state_id == state_id,
            )
            .with_for_update(of=Bookmark)
        )
    ).one_or_none()
    if row is None:
        raise ReaderBookmarkNotFound("Bookmark does not exist")

    bookmark, section = row
    return ReaderBookmarkRecord(bookmark=bookmark, section=section)


__all__ = ["ReaderBookmarkConflict", "remove_bookmark_if_current"]
