from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import DocumentSection
from bukmatika.persistence.reader_models import Highlight, ReadingState
from bukmatika.persistence.readers import (
    ReaderAccess,
    ReaderHighlightNotFound,
    ReaderHighlightRecord,
)


class ReaderHighlightConflict(RuntimeError):
    """The highlight changed after the caller loaded its editable revision."""


async def update_highlight_note_if_current(
    session: AsyncSession,
    *,
    access: ReaderAccess,
    highlight_id: UUID,
    note: str | None,
    expected_updated_at: datetime | None,
) -> ReaderHighlightRecord:
    state_id = await session.scalar(
        select(ReadingState.id).where(
            ReadingState.library_entry_id == access.library_entry_id,
            ReadingState.document_id == access.document.id,
        )
    )
    if state_id is None:
        raise ReaderHighlightNotFound("Highlight does not exist")

    row = (
        await session.execute(
            select(Highlight, DocumentSection)
            .join(DocumentSection, DocumentSection.id == Highlight.section_id)
            .where(
                Highlight.id == highlight_id,
                Highlight.reading_state_id == state_id,
            )
            .with_for_update(of=Highlight)
        )
    ).one_or_none()
    if row is None:
        raise ReaderHighlightNotFound("Highlight does not exist")

    highlight, section = row
    if expected_updated_at is not None and highlight.updated_at != expected_updated_at:
        raise ReaderHighlightConflict("Highlight note changed after this edit started")

    highlight.note = note
    highlight.updated_at = datetime.now(UTC)
    await session.flush()
    return ReaderHighlightRecord(highlight=highlight, section=section)


__all__ = ["ReaderHighlightConflict", "update_highlight_note_if_current"]
