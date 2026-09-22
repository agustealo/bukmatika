from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry
from bukmatika.persistence.reader_models import Bookmark, ReadingState


class ReaderAccessDenied(LookupError):
    pass


class ReaderPositionInvalid(ValueError):
    pass


class ReaderBookmarkNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ReaderAccess:
    library_entry_id: UUID
    principal_id: UUID
    document: Document


class ReaderRepository:
    """Canonical persistence authority for document reading state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_access(self, library_entry_id: UUID, document_id: UUID) -> ReaderAccess:
        row = (
            await self._session.execute(
                select(LibraryEntry, Document)
                .join(Asset, Asset.id == Document.asset_id)
                .join(Edition, Edition.id == Asset.edition_id)
                .where(
                    LibraryEntry.id == library_entry_id,
                    Document.id == document_id,
                    LibraryEntry.work_id == Edition.work_id,
                    or_(
                        LibraryEntry.edition_id.is_(None),
                        and_(
                            LibraryEntry.edition_id.is_not(None),
                            LibraryEntry.edition_id == Edition.id,
                        ),
                    ),
                )
            )
        ).one_or_none()
        if row is None:
            raise ReaderAccessDenied("Library entry does not own this processed document")
        library_entry, document = row
        return ReaderAccess(
            library_entry_id=library_entry.id,
            principal_id=library_entry.principal_id,
            document=document,
        )

    async def sections(
        self,
        document_id: UUID,
        *,
        after_ordinal: int | None,
        limit: int,
    ) -> list[DocumentSection]:
        query = select(DocumentSection).where(DocumentSection.document_id == document_id)
        if after_ordinal is not None:
            query = query.where(DocumentSection.ordinal > after_ordinal)
        return list(
            (
                await self._session.scalars(
                    query.order_by(DocumentSection.ordinal).limit(limit)
                )
            ).all()
        )

    async def state_for(
        self,
        library_entry_id: UUID,
        document_id: UUID,
    ) -> ReadingState | None:
        return await self._session.scalar(
            select(ReadingState).where(
                ReadingState.library_entry_id == library_entry_id,
                ReadingState.document_id == document_id,
            )
        )

    async def bookmarks_for(
        self,
        library_entry_id: UUID,
        document_id: UUID,
    ) -> list[Bookmark]:
        state = await self.state_for(library_entry_id, document_id)
        if state is None:
            return []
        return list(
            (
                await self._session.scalars(
                    select(Bookmark)
                    .where(Bookmark.reading_state_id == state.id)
                    .order_by(Bookmark.created_at, Bookmark.id)
                )
            ).all()
        )

    async def save_progress(
        self,
        *,
        access: ReaderAccess,
        section_id: UUID,
        char_offset: int,
        progress_fraction: float,
    ) -> ReadingState:
        section = await self._validated_section(
            document_id=access.document.id,
            section_id=section_id,
            char_offset=char_offset,
        )
        now = datetime.now(UTC)
        status = _reading_status(progress_fraction)
        statement = (
            insert(ReadingState)
            .values(
                library_entry_id=access.library_entry_id,
                document_id=access.document.id,
                status=status,
                progress_fraction=progress_fraction,
                section_id=section.id,
                section_ordinal=section.ordinal,
                char_offset=char_offset,
                locator=section.locator,
                last_read_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_reading_state_library_document",
                set_={
                    "status": status,
                    "progress_fraction": progress_fraction,
                    "section_id": section.id,
                    "section_ordinal": section.ordinal,
                    "char_offset": char_offset,
                    "locator": section.locator,
                    "last_read_at": now,
                    "updated_at": now,
                },
            )
            .returning(ReadingState)
        )
        return (await self._session.execute(statement)).scalar_one()

    async def add_bookmark(
        self,
        *,
        access: ReaderAccess,
        section_id: UUID,
        char_offset: int,
        label: str | None,
    ) -> Bookmark:
        section = await self._validated_section(
            document_id=access.document.id,
            section_id=section_id,
            char_offset=char_offset,
        )
        state = await self.state_for(access.library_entry_id, access.document.id)
        if state is None:
            state = await self.save_progress(
                access=access,
                section_id=section.id,
                char_offset=char_offset,
                progress_fraction=0.0,
            )
        statement = (
            insert(Bookmark)
            .values(
                reading_state_id=state.id,
                section_id=section.id,
                char_offset=char_offset,
                locator=section.locator,
                label=label,
            )
            .on_conflict_do_update(
                constraint="uq_bookmark_position",
                set_={
                    "label": label,
                    "locator": section.locator,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Bookmark)
        )
        return (await self._session.execute(statement)).scalar_one()

    async def remove_bookmark(
        self,
        *,
        access: ReaderAccess,
        bookmark_id: UUID,
    ) -> None:
        state = await self.state_for(access.library_entry_id, access.document.id)
        if state is None:
            raise ReaderBookmarkNotFound("Bookmark does not exist")
        deleted_id = await self._session.scalar(
            delete(Bookmark)
            .where(
                Bookmark.id == bookmark_id,
                Bookmark.reading_state_id == state.id,
            )
            .returning(Bookmark.id)
        )
        if deleted_id is None:
            raise ReaderBookmarkNotFound("Bookmark does not exist")

    async def _validated_section(
        self,
        *,
        document_id: UUID,
        section_id: UUID,
        char_offset: int,
    ) -> DocumentSection:
        section = await self._session.scalar(
            select(DocumentSection).where(
                DocumentSection.id == section_id,
                DocumentSection.document_id == document_id,
            )
        )
        if section is None:
            raise ReaderPositionInvalid("Reader position references a section outside the document")
        if char_offset < 0 or char_offset > len(section.text):
            raise ReaderPositionInvalid("Reader character offset is outside the section text")
        return section


def _reading_status(progress_fraction: float) -> str:
    if progress_fraction <= 0:
        return "unread"
    if progress_fraction >= 1:
        return "finished"
    return "reading"
