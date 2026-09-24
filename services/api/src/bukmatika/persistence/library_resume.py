from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.models import Asset, Edition, LibraryEntry
from bukmatika.persistence.reader_models import ReadingState


@dataclass(frozen=True, slots=True)
class LibraryResume:
    document: Document
    asset: Asset
    state: ReadingState


class LibraryResumeRepository:
    """Read projection selecting the most recent actual reading state for a LibraryEntry."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_for_entry(self, entry: LibraryEntry) -> LibraryResume | None:
        statement = (
            select(Document, Asset, ReadingState)
            .join(ReadingState, ReadingState.document_id == Document.id)
            .join(Asset, Asset.id == Document.asset_id)
            .join(Edition, Edition.id == Asset.edition_id)
            .where(
                ReadingState.library_entry_id == entry.id,
                Edition.work_id == entry.work_id,
                or_(
                    ReadingState.last_read_at.is_not(None),
                    ReadingState.status != "unread",
                    ReadingState.progress_fraction > 0,
                ),
            )
        )
        if entry.edition_id is not None:
            statement = statement.where(Edition.id == entry.edition_id)
        row = (
            await self._session.execute(
                statement.order_by(
                    ReadingState.last_read_at.desc().nullslast(),
                    ReadingState.updated_at.desc(),
                    ReadingState.id.desc(),
                ).limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        document, asset, state = row
        return LibraryResume(document=document, asset=asset, state=state)
