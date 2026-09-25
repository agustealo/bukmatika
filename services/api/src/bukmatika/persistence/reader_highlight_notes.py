from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select
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


async def _locked_highlight_record(
    session: AsyncSession,
    *,
    access: ReaderAccess,
    highlight_id: UUID,
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
    return ReaderHighlightRecord(highlight=highlight, section=section)


async def update_highlight_note_if_current(
    session: AsyncSession,
    *,
    access: ReaderAccess,
    highlight_id: UUID,
    note: str | None,
    expected_updated_at: datetime | None,
) -> ReaderHighlightRecord:
    record = await _locked_highlight_record(
        session,
        access=access,
        highlight_id=highlight_id,
    )
    if expected_updated_at is not None and record.highlight.updated_at != expected_updated_at:
        raise ReaderHighlightConflict("Highlight note changed after this edit started")

    record.highlight.note = note
    record.highlight.updated_at = datetime.now(UTC)
    await session.flush()
    return record


async def delete_highlight_if_current(
    session: AsyncSession,
    *,
    access: ReaderAccess,
    highlight_id: UUID,
    expected_updated_at: datetime | None,
) -> None:
    record = await _locked_highlight_record(
        session,
        access=access,
        highlight_id=highlight_id,
    )
    if expected_updated_at is not None and record.highlight.updated_at != expected_updated_at:
        raise ReaderHighlightConflict("Highlight changed after this delete started")

    await session.execute(delete(Highlight).where(Highlight.id == record.highlight.id))
    await session.flush()


__all__ = [
    "ReaderHighlightConflict",
    "delete_highlight_if_current",
    "update_highlight_note_if_current",
]
