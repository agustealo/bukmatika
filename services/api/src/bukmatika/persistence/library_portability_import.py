from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.library_organization_models import (
    LibraryCollection,
    LibrarySmartShelf,
    LibraryTag,
)
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    Identifier,
    LibraryEntry,
    SourceRecord,
    SourceRecordLink,
    Work,
    WorkContributor,
)
from bukmatika.persistence.reader_models import ReadingState


class LibraryImportPlanningRepository:
    """Read-only identity and conflict projection for byte-free library imports."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def work_ids_for_identifier(self, scheme: str, normalized_value: str) -> set[UUID]:
        values = await self._session.scalars(
            select(Identifier.entity_id).where(
                Identifier.entity_type == "work",
                Identifier.scheme == scheme,
                Identifier.normalized_value == normalized_value,
            )
        )
        return set(values.all())

    async def edition_ids_for_identifier(self, scheme: str, normalized_value: str) -> set[UUID]:
        values = await self._session.scalars(
            select(Identifier.entity_id).where(
                Identifier.entity_type == "edition",
                Identifier.scheme == scheme,
                Identifier.normalized_value == normalized_value,
            )
        )
        return set(values.all())

    async def work_ids_for_sources(self, sources: list[tuple[str, str]]) -> set[UUID]:
        work_ids: set[UUID] = set()
        edition_ids: set[UUID] = set()
        for provider, provider_record_id in sources:
            rows = (
                await self._session.execute(
                    select(SourceRecordLink.entity_type, SourceRecordLink.entity_id)
                    .join(
                        SourceRecord,
                        SourceRecord.id == SourceRecordLink.source_record_id,
                    )
                    .where(
                        SourceRecord.provider == provider,
                        SourceRecord.provider_record_id == provider_record_id,
                    )
                )
            ).all()
            for entity_type, entity_id in rows:
                if entity_type == "work":
                    work_ids.add(entity_id)
                elif entity_type == "edition":
                    edition_ids.add(entity_id)
        if edition_ids:
            linked = await self._session.scalars(
                select(Edition.work_id).where(Edition.id.in_(edition_ids))
            )
            work_ids.update(linked.all())
        return work_ids

    async def edition_ids_for_sources(self, sources: list[tuple[str, str]]) -> set[UUID]:
        if not sources:
            return set()
        result: set[UUID] = set()
        for provider, provider_record_id in sources:
            values = await self._session.scalars(
                select(SourceRecordLink.entity_id)
                .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
                .where(
                    SourceRecord.provider == provider,
                    SourceRecord.provider_record_id == provider_record_id,
                    SourceRecordLink.entity_type == "edition",
                )
            )
            result.update(values.all())
        return result

    async def work_ids_for_metadata(
        self,
        *,
        normalized_title: str,
        normalized_author: str | None,
    ) -> set[UUID]:
        query = select(Work.id).where(Work.normalized_title == normalized_title)
        if normalized_author is not None:
            query = (
                query.join(WorkContributor, WorkContributor.work_id == Work.id)
                .join(Contributor, Contributor.id == WorkContributor.contributor_id)
                .where(
                    WorkContributor.role == "author",
                    Contributor.normalized_name == normalized_author,
                )
            )
        values = await self._session.scalars(query.distinct().limit(3))
        return set(values.all())

    async def editions_for_work(self, work_id: UUID) -> list[Edition]:
        values = await self._session.scalars(
            select(Edition).where(Edition.work_id == work_id).order_by(Edition.id)
        )
        return list(values)

    async def edition_work_id(self, edition_id: UUID) -> UUID | None:
        return await self._session.scalar(
            select(Edition.work_id).where(Edition.id == edition_id)
        )

    async def documents_for_sha(
        self,
        *,
        source_sha256: str,
        work_id: UUID,
        edition_id: UUID | None,
    ) -> list[Document]:
        query = (
            select(Document)
            .join(Asset, Asset.id == Document.asset_id)
            .join(Edition, Edition.id == Asset.edition_id)
            .where(
                Document.source_sha256 == source_sha256,
                Edition.work_id == work_id,
            )
        )
        if edition_id is not None:
            query = query.where(Edition.id == edition_id)
        values = await self._session.scalars(query.order_by(Document.id))
        return list(values)

    async def library_entry_id(
        self,
        *,
        principal_id: UUID,
        work_id: UUID,
        edition_id: UUID | None,
    ) -> UUID | None:
        query = select(LibraryEntry.id).where(
            LibraryEntry.principal_id == principal_id,
            LibraryEntry.work_id == work_id,
        )
        if edition_id is None:
            query = query.where(LibraryEntry.edition_id.is_(None))
        else:
            query = query.where(LibraryEntry.edition_id == edition_id)
        return await self._session.scalar(query)

    async def collection_by_normalized_name(
        self,
        *,
        principal_id: UUID,
        normalized_name: str,
    ) -> LibraryCollection | None:
        return await self._session.scalar(
            select(LibraryCollection).where(
                LibraryCollection.principal_id == principal_id,
                LibraryCollection.normalized_name == normalized_name,
            )
        )

    async def tag_by_normalized_name(
        self,
        *,
        principal_id: UUID,
        normalized_name: str,
    ) -> LibraryTag | None:
        return await self._session.scalar(
            select(LibraryTag).where(
                LibraryTag.principal_id == principal_id,
                LibraryTag.normalized_name == normalized_name,
            )
        )

    async def smart_shelf_by_normalized_name(
        self,
        *,
        principal_id: UUID,
        normalized_name: str,
    ) -> LibrarySmartShelf | None:
        return await self._session.scalar(
            select(LibrarySmartShelf).where(
                LibrarySmartShelf.principal_id == principal_id,
                LibrarySmartShelf.normalized_name == normalized_name,
            )
        )

    async def reading_state(
        self,
        *,
        library_entry_id: UUID,
        document_id: UUID,
    ) -> ReadingState | None:
        return await self._session.scalar(
            select(ReadingState).where(
                ReadingState.library_entry_id == library_entry_id,
                ReadingState.document_id == document_id,
            )
        )

    async def section_by_ordinal(
        self,
        *,
        document_id: UUID,
        ordinal: int,
    ) -> DocumentSection | None:
        return await self._session.scalar(
            select(DocumentSection).where(
                DocumentSection.document_id == document_id,
                DocumentSection.ordinal == ordinal,
            )
        )
