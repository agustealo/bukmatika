from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry
from bukmatika.persistence.reader_models import Bookmark, Highlight, ReadingState
from bukmatika.reader_progress import (
    CanonicalReaderProgressError,
    canonical_progress_fraction,
    canonical_reading_status,
)


class ReaderAccessDenied(LookupError):
    pass


class ReaderPositionInvalid(ValueError):
    pass


class ReaderBookmarkNotFound(LookupError):
    pass


class ReaderHighlightNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ReaderAccess:
    library_entry_id: UUID
    principal_id: UUID
    document: Document


@dataclass(frozen=True, slots=True)
class ReaderBookmarkRecord:
    bookmark: Bookmark
    section: DocumentSection


@dataclass(frozen=True, slots=True)
class ReaderHighlightRecord:
    highlight: Highlight
    section: DocumentSection


@dataclass(frozen=True, slots=True)
class ReaderNavigationSource:
    section_id: UUID
    ordinal: int
    heading: str | None
    locator: dict[str, Any]


class ReaderRepository:
    """Canonical persistence authority for principal-scoped document reading state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_access(
        self,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
    ) -> ReaderAccess:
        row = (
            await self._session.execute(
                select(LibraryEntry, Document)
                .join(Asset, Asset.id == Document.asset_id)
                .join(Edition, Edition.id == Asset.edition_id)
                .where(
                    LibraryEntry.id == library_entry_id,
                    LibraryEntry.principal_id == principal_id,
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
            raise ReaderAccessDenied("Principal does not own this processed document")
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

    async def navigation_sources(self, document_id: UUID) -> list[ReaderNavigationSource]:
        rows = (
            await self._session.execute(
                select(
                    DocumentSection.id,
                    DocumentSection.ordinal,
                    DocumentSection.heading,
                    DocumentSection.locator,
                )
                .where(DocumentSection.document_id == document_id)
                .order_by(DocumentSection.ordinal)
            )
        ).tuples()
        return [
            ReaderNavigationSource(
                section_id=section_id,
                ordinal=ordinal,
                heading=heading,
                locator=locator,
            )
            for section_id, ordinal, heading, locator in rows
        ]

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
    ) -> list[ReaderBookmarkRecord]:
        state = await self.state_for(library_entry_id, document_id)
        if state is None:
            return []
        rows = (
            await self._session.execute(
                select(Bookmark, DocumentSection)
                .join(DocumentSection, DocumentSection.id == Bookmark.section_id)
                .where(Bookmark.reading_state_id == state.id)
                .order_by(Bookmark.created_at, Bookmark.id)
            )
        ).all()
        return [
            ReaderBookmarkRecord(bookmark=bookmark, section=section)
            for bookmark, section in rows
        ]

    async def highlights_for(
        self,
        library_entry_id: UUID,
        document_id: UUID,
    ) -> list[ReaderHighlightRecord]:
        state = await self.state_for(library_entry_id, document_id)
        if state is None:
            return []
        rows = (
            await self._session.execute(
                select(Highlight, DocumentSection)
                .join(DocumentSection, DocumentSection.id == Highlight.section_id)
                .where(Highlight.reading_state_id == state.id)
                .order_by(Highlight.created_at, Highlight.id)
            )
        ).all()
        return [
            ReaderHighlightRecord(highlight=highlight, section=section)
            for highlight, section in rows
        ]

    async def save_progress(
        self,
        *,
        access: ReaderAccess,
        section_id: UUID,
        char_offset: int,
    ) -> ReadingState:
        section = await self._validated_section(
            document_id=access.document.id,
            section_id=section_id,
            char_offset=char_offset,
        )
        try:
            progress_fraction = canonical_progress_fraction(
                section_count=access.document.section_count,
                section_ordinal=section.ordinal,
                section_text_length=len(section.text),
                char_offset=char_offset,
            )
        except CanonicalReaderProgressError as exc:
            raise ReaderPositionInvalid(str(exc)) from exc
        now = datetime.now(UTC)
        status = canonical_reading_status(progress_fraction)
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
    ) -> ReaderBookmarkRecord:
        section = await self._validated_section(
            document_id=access.document.id,
            section_id=section_id,
            char_offset=char_offset,
        )
        state = await self._state_for_write(
            access=access,
            section=section,
            char_offset=char_offset,
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
        bookmark = (await self._session.execute(statement)).scalar_one()
        return ReaderBookmarkRecord(bookmark=bookmark, section=section)

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

    async def add_highlight(
        self,
        *,
        access: ReaderAccess,
        section_id: UUID,
        char_start: int,
        char_end: int,
        note: str | None,
    ) -> ReaderHighlightRecord:
        section = await self._validated_range(
            document_id=access.document.id,
            section_id=section_id,
            char_start=char_start,
            char_end=char_end,
        )
        state = await self._state_for_write(
            access=access,
            section=section,
            char_offset=char_start,
        )
        statement = (
            insert(Highlight)
            .values(
                reading_state_id=state.id,
                section_id=section.id,
                char_start=char_start,
                char_end=char_end,
                locator=section.locator,
                note=note,
            )
            .on_conflict_do_update(
                constraint="uq_highlight_range",
                set_={
                    "note": note,
                    "locator": section.locator,
                    "updated_at": datetime.now(UTC),
                },
            )
            .returning(Highlight)
        )
        highlight = (await self._session.execute(statement)).scalar_one()
        return ReaderHighlightRecord(highlight=highlight, section=section)

    async def update_highlight_note(
        self,
        *,
        access: ReaderAccess,
        highlight_id: UUID,
        note: str | None,
    ) -> ReaderHighlightRecord:
        record = await self._highlight_record(access=access, highlight_id=highlight_id)
        record.highlight.note = note
        record.highlight.updated_at = datetime.now(UTC)
        await self._session.flush()
        return record

    async def remove_highlight(
        self,
        *,
        access: ReaderAccess,
        highlight_id: UUID,
    ) -> None:
        state = await self.state_for(access.library_entry_id, access.document.id)
        if state is None:
            raise ReaderHighlightNotFound("Highlight does not exist")
        deleted_id = await self._session.scalar(
            delete(Highlight)
            .where(
                Highlight.id == highlight_id,
                Highlight.reading_state_id == state.id,
            )
            .returning(Highlight.id)
        )
        if deleted_id is None:
            raise ReaderHighlightNotFound("Highlight does not exist")

    async def _highlight_record(
        self,
        *,
        access: ReaderAccess,
        highlight_id: UUID,
    ) -> ReaderHighlightRecord:
        state = await self.state_for(access.library_entry_id, access.document.id)
        if state is None:
            raise ReaderHighlightNotFound("Highlight does not exist")
        row = (
            await self._session.execute(
                select(Highlight, DocumentSection)
                .join(DocumentSection, DocumentSection.id == Highlight.section_id)
                .where(
                    Highlight.id == highlight_id,
                    Highlight.reading_state_id == state.id,
                )
            )
        ).one_or_none()
        if row is None:
            raise ReaderHighlightNotFound("Highlight does not exist")
        highlight, section = row
        return ReaderHighlightRecord(highlight=highlight, section=section)

    async def _state_for_write(
        self,
        *,
        access: ReaderAccess,
        section: DocumentSection,
        char_offset: int,
    ) -> ReadingState:
        state = await self.state_for(access.library_entry_id, access.document.id)
        if state is not None:
            return state

        statement = (
            insert(ReadingState)
            .values(
                library_entry_id=access.library_entry_id,
                document_id=access.document.id,
                status="unread",
                progress_fraction=0.0,
                section_id=section.id,
                section_ordinal=section.ordinal,
                char_offset=char_offset,
                locator=section.locator,
                last_read_at=None,
            )
            .on_conflict_do_nothing(constraint="uq_reading_state_library_document")
            .returning(ReadingState)
        )
        created = (await self._session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created

        state = await self.state_for(access.library_entry_id, access.document.id)
        if state is None:
            raise RuntimeError("Reading state disappeared during annotation creation")
        return state

    async def _validated_section(
        self,
        *,
        document_id: UUID,
        section_id: UUID,
        char_offset: int,
    ) -> DocumentSection:
        section = await self._section(document_id=document_id, section_id=section_id)
        if char_offset < 0 or char_offset > len(section.text):
            raise ReaderPositionInvalid("Reader character offset is outside the section text")
        return section

    async def _validated_range(
        self,
        *,
        document_id: UUID,
        section_id: UUID,
        char_start: int,
        char_end: int,
    ) -> DocumentSection:
        section = await self._section(document_id=document_id, section_id=section_id)
        if char_start < 0 or char_end <= char_start or char_end > len(section.text):
            raise ReaderPositionInvalid("Highlight range is outside the section text")
        return section

    async def _section(self, *, document_id: UUID, section_id: UUID) -> DocumentSection:
        section = await self._session.scalar(
            select(DocumentSection).where(
                DocumentSection.id == section_id,
                DocumentSection.document_id == document_id,
            )
        )
        if section is None:
            raise ReaderPositionInvalid("Reader position references a section outside the document")
        return section
