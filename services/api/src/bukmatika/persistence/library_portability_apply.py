from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import DocumentSection
from bukmatika.persistence.library_organization_models import LibraryEntryTag, LibraryTag
from bukmatika.persistence.models import SourceRecord, SourceRecordLink
from bukmatika.persistence.reader_models import Bookmark, Highlight, ReadingState


class LibraryImportApplyConflict(RuntimeError):
    def __init__(self, *, source_id: UUID, code: str, detail: str) -> None:
        super().__init__(detail)
        self.source_id = source_id
        self.code = code
        self.detail = detail


class LibraryImportApplyRepository:
    """Import-specific writes into existing canonical authorities.

    This repository creates no portability-owned persistence. It only performs
    race-safe, idempotent writes against the canonical catalog, library,
    organization, and reader tables after the import planner has accepted the
    manifest in the same database transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_source_reference(
        self,
        *,
        provider: str,
        provider_record_id: str,
        canonical_url: str,
        relationship: str,
        entity_type: str,
        entity_id: UUID,
    ) -> None:
        source = await self._session.scalar(
            select(SourceRecord).where(
                SourceRecord.provider == provider,
                SourceRecord.provider_record_id == provider_record_id,
            )
        )
        if source is None:
            statement = (
                insert(SourceRecord)
                .values(
                    provider=provider,
                    provider_record_id=provider_record_id,
                    canonical_url=canonical_url,
                )
                .on_conflict_do_nothing(constraint="uq_source_provider_record")
                .returning(SourceRecord)
            )
            source = (await self._session.execute(statement)).scalar_one_or_none()
            if source is None:
                source = await self._session.scalar(
                    select(SourceRecord).where(
                        SourceRecord.provider == provider,
                        SourceRecord.provider_record_id == provider_record_id,
                    )
                )
        if source is None:
            raise RuntimeError("Source record upsert returned no row")

        await self._session.execute(
            insert(SourceRecordLink)
            .values(
                source_record_id=source.id,
                entity_type=entity_type,
                entity_id=entity_id,
                relationship=relationship,
            )
            .on_conflict_do_nothing(constraint="uq_source_record_target")
        )

    async def create_tag(
        self,
        *,
        principal_id: UUID,
        name: str,
        normalized_name: str,
    ) -> LibraryTag:
        statement = (
            insert(LibraryTag)
            .values(
                principal_id=principal_id,
                name=name,
                normalized_name=normalized_name,
            )
            .on_conflict_do_nothing(constraint="uq_library_tag_principal_name")
            .returning(LibraryTag)
        )
        tag = (await self._session.execute(statement)).scalar_one_or_none()
        if tag is None:
            tag = await self._session.scalar(
                select(LibraryTag).where(
                    LibraryTag.principal_id == principal_id,
                    LibraryTag.normalized_name == normalized_name,
                )
            )
        if tag is None:
            raise RuntimeError("Tag upsert returned no row")
        return tag

    async def add_tag_entry(self, *, library_entry_id: UUID, tag_id: UUID) -> None:
        await self._session.execute(
            insert(LibraryEntryTag)
            .values(library_entry_id=library_entry_id, tag_id=tag_id)
            .on_conflict_do_nothing(
                index_elements=[LibraryEntryTag.library_entry_id, LibraryEntryTag.tag_id]
            )
        )

    async def section_by_ordinal(
        self,
        *,
        document_id: UUID,
        ordinal: int,
    ) -> DocumentSection:
        section = await self._session.scalar(
            select(DocumentSection).where(
                DocumentSection.document_id == document_id,
                DocumentSection.ordinal == ordinal,
            )
        )
        if section is None:
            raise RuntimeError("Planner-approved section disappeared during import")
        return section

    async def apply_reading_state(
        self,
        *,
        source_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        status: str,
        progress_fraction: float,
        section: DocumentSection | None,
        char_offset: int | None,
        last_read_at: datetime | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> ReadingState:
        statement = (
            insert(ReadingState)
            .values(
                library_entry_id=library_entry_id,
                document_id=document_id,
                status=status,
                progress_fraction=progress_fraction,
                section_id=section.id if section is not None else None,
                section_ordinal=section.ordinal if section is not None else None,
                char_offset=char_offset,
                locator=section.locator if section is not None else {},
                last_read_at=last_read_at,
                created_at=created_at,
                updated_at=updated_at,
            )
            .on_conflict_do_update(
                constraint="uq_reading_state_library_document",
                set_={
                    "status": status,
                    "progress_fraction": progress_fraction,
                    "section_id": section.id if section is not None else None,
                    "section_ordinal": section.ordinal if section is not None else None,
                    "char_offset": char_offset,
                    "locator": section.locator if section is not None else {},
                    "last_read_at": last_read_at,
                    "updated_at": updated_at,
                },
                where=ReadingState.updated_at <= updated_at,
            )
            .returning(ReadingState)
        )
        state = (await self._session.execute(statement)).scalar_one_or_none()
        if state is None:
            raise LibraryImportApplyConflict(
                source_id=source_id,
                code="local_reader_state_changed",
                detail="Local reader state became newer while the import was being applied",
            )
        return state

    async def apply_bookmark(
        self,
        *,
        source_id: UUID,
        reading_state_id: UUID,
        section: DocumentSection,
        char_offset: int,
        label: str | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        statement = (
            insert(Bookmark)
            .values(
                reading_state_id=reading_state_id,
                section_id=section.id,
                char_offset=char_offset,
                locator=section.locator,
                label=label,
                created_at=created_at,
                updated_at=updated_at,
            )
            .on_conflict_do_update(
                constraint="uq_bookmark_position",
                set_={
                    "label": label,
                    "locator": section.locator,
                    "updated_at": updated_at,
                },
                where=Bookmark.updated_at <= updated_at,
            )
            .returning(Bookmark.id)
        )
        bookmark_id = (await self._session.execute(statement)).scalar_one_or_none()
        if bookmark_id is None:
            raise LibraryImportApplyConflict(
                source_id=source_id,
                code="local_bookmark_newer",
                detail="A local bookmark at the same position is newer than the imported bookmark",
            )

    async def apply_highlight(
        self,
        *,
        source_id: UUID,
        reading_state_id: UUID,
        section: DocumentSection,
        char_start: int,
        char_end: int,
        note: str | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        statement = (
            insert(Highlight)
            .values(
                reading_state_id=reading_state_id,
                section_id=section.id,
                char_start=char_start,
                char_end=char_end,
                locator=section.locator,
                note=note,
                created_at=created_at,
                updated_at=updated_at,
            )
            .on_conflict_do_update(
                constraint="uq_highlight_range",
                set_={
                    "note": note,
                    "locator": section.locator,
                    "updated_at": updated_at,
                },
                where=Highlight.updated_at <= updated_at,
            )
            .returning(Highlight.id)
        )
        highlight_id = (await self._session.execute(statement)).scalar_one_or_none()
        if highlight_id is None:
            raise LibraryImportApplyConflict(
                source_id=source_id,
                code="local_highlight_newer",
                detail="A local highlight at the same range is newer than the imported highlight",
            )
