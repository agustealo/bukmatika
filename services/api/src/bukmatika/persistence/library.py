from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentProcessingState
from bukmatika.persistence.jobs import Job
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Contributor,
    Edition,
    LibraryEntry,
    RightsDecision,
    RightsEvidenceRecord,
    RightsEvidenceSubject,
    SourceRecord,
    SourceRecordLink,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)
from bukmatika.persistence.reader_models import ReadingState


class LibraryTargetNotFound(LookupError):
    pass


class DossierNotFound(LookupError):
    pass


class DossierIdentityConflict(RuntimeError):
    pass


class LibraryRepository:
    """Canonical ownership and dossier read model over existing durable authorities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_source_work_id(self, provider: str, provider_record_id: str) -> UUID:
        source_id = await self._session.scalar(
            select(SourceRecord.id).where(
                SourceRecord.provider == provider,
                SourceRecord.provider_record_id == provider_record_id,
            )
        )
        if source_id is None:
            raise DossierNotFound("Source record is not in the canonical catalog")

        rows = (
            await self._session.execute(
                select(SourceRecordLink.entity_type, SourceRecordLink.entity_id).where(
                    SourceRecordLink.source_record_id == source_id
                )
            )
        ).all()
        work_ids: set[UUID] = set()
        edition_ids: list[UUID] = []
        for entity_type, entity_id in rows:
            if entity_type == "work":
                work_ids.add(entity_id)
            elif entity_type == "edition":
                edition_ids.append(entity_id)

        if edition_ids:
            linked_work_ids = await self._session.scalars(
                select(Edition.work_id).where(Edition.id.in_(edition_ids))
            )
            work_ids.update(linked_work_ids.all())

        if not work_ids:
            raise DossierNotFound("Source record has no canonical work link")
        if len(work_ids) != 1:
            raise DossierIdentityConflict("Source record resolves to multiple canonical works")
        return next(iter(work_ids))

    async def get_work(self, work_id: UUID) -> Work | None:
        return await self._session.get(Work, work_id)

    async def authors_for_work(self, work_id: UUID) -> list[str]:
        values = await self._session.scalars(
            select(Contributor.display_name)
            .join(WorkContributor, WorkContributor.contributor_id == Contributor.id)
            .where(
                WorkContributor.work_id == work_id,
                WorkContributor.role == "author",
            )
            .order_by(Contributor.display_name)
        )
        return list(values)

    async def subjects_for_work(self, work_id: UUID) -> list[str]:
        values = await self._session.scalars(
            select(Subject.display_name)
            .join(WorkSubject, WorkSubject.subject_id == Subject.id)
            .where(WorkSubject.work_id == work_id)
            .order_by(Subject.display_name)
        )
        return list(values)

    async def editions_for_work(self, work_id: UUID) -> list[Edition]:
        values = await self._session.scalars(
            select(Edition)
            .where(Edition.work_id == work_id)
            .order_by(Edition.publication_year.asc().nullslast(), Edition.title, Edition.id)
        )
        return list(values)

    async def assets_for_edition(self, edition_id: UUID) -> list[Asset]:
        values = await self._session.scalars(
            select(Asset).where(Asset.edition_id == edition_id).order_by(Asset.format, Asset.id)
        )
        return list(values)

    async def work_library_entry_id(self, principal_id: UUID, work_id: UUID) -> UUID | None:
        return await self._session.scalar(
            select(LibraryEntry.id).where(
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.work_id == work_id,
                LibraryEntry.edition_id.is_(None),
            )
        )

    async def edition_library_entry_id(
        self,
        principal_id: UUID,
        edition_id: UUID,
    ) -> UUID | None:
        return await self._session.scalar(
            select(LibraryEntry.id).where(
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.edition_id == edition_id,
            )
        )

    async def save_work(self, principal_id: UUID, work_id: UUID) -> LibraryEntry:
        if await self._session.get(Work, work_id) is None:
            raise LibraryTargetNotFound("Work does not exist")
        statement = (
            insert(LibraryEntry)
            .values(
                principal_id=principal_id,
                work_id=work_id,
                edition_id=None,
                status="saved",
            )
            .on_conflict_do_nothing(
                index_elements=[LibraryEntry.principal_id, LibraryEntry.work_id],
                index_where=LibraryEntry.edition_id.is_(None),
            )
            .returning(LibraryEntry)
        )
        created = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self._session.scalar(
            select(LibraryEntry).where(
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.work_id == work_id,
                LibraryEntry.edition_id.is_(None),
            )
        )
        if existing is None:
            raise RuntimeError("Work library upsert returned no row")
        return existing

    async def save_edition(self, principal_id: UUID, edition_id: UUID) -> LibraryEntry:
        edition = await self._session.get(Edition, edition_id)
        if edition is None:
            raise LibraryTargetNotFound("Edition does not exist")
        statement = (
            insert(LibraryEntry)
            .values(
                principal_id=principal_id,
                work_id=edition.work_id,
                edition_id=edition.id,
                status="saved",
            )
            .on_conflict_do_nothing(
                index_elements=[LibraryEntry.principal_id, LibraryEntry.edition_id],
                index_where=LibraryEntry.edition_id.is_not(None),
            )
            .returning(LibraryEntry)
        )
        created = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self._session.scalar(
            select(LibraryEntry).where(
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.edition_id == edition.id,
            )
        )
        if existing is None:
            raise RuntimeError("Edition library upsert returned no row")
        return existing

    async def library_entries(self, principal_id: UUID) -> list[LibraryEntry]:
        values = await self._session.scalars(
            select(LibraryEntry)
            .where(LibraryEntry.principal_id == principal_id)
            .order_by(LibraryEntry.updated_at.desc(), LibraryEntry.id)
        )
        return list(values)

    async def acquisition_for_asset(self, asset_id: UUID) -> Acquisition | None:
        return await self._session.scalar(
            select(Acquisition).where(Acquisition.asset_id == asset_id)
        )

    async def processing_for_asset(self, asset_id: UUID) -> DocumentProcessingState | None:
        return await self._session.scalar(
            select(DocumentProcessingState).where(DocumentProcessingState.asset_id == asset_id)
        )

    async def document_for_asset(self, asset_id: UUID) -> Document | None:
        return await self._session.scalar(select(Document).where(Document.asset_id == asset_id))

    async def latest_job_for_asset(self, *, job_type: str, asset_id: UUID) -> Job | None:
        return await self._session.scalar(
            select(Job)
            .where(
                Job.job_type == job_type,
                Job.payload["asset_id"].as_string() == str(asset_id),
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )

    async def latest_ocr_job_for_asset(self, asset_id: UUID) -> Job | None:
        return await self.latest_job_for_asset(job_type="document_ocr", asset_id=asset_id)

    async def latest_rights_decision_for_asset(self, asset_id: UUID) -> RightsDecision | None:
        return await self._session.scalar(
            select(RightsDecision)
            .where(
                RightsDecision.subject_type == "asset",
                RightsDecision.subject_id == asset_id,
            )
            .order_by(RightsDecision.evaluated_at.desc(), RightsDecision.id.desc())
            .limit(1)
        )

    async def rights_evidence_for_asset(self, asset_id: UUID) -> list[RightsEvidenceRecord]:
        values = await self._session.scalars(
            select(RightsEvidenceRecord)
            .join(
                RightsEvidenceSubject,
                RightsEvidenceSubject.rights_evidence_id == RightsEvidenceRecord.id,
            )
            .where(
                RightsEvidenceSubject.subject_type == "asset",
                RightsEvidenceSubject.subject_id == asset_id,
            )
            .order_by(RightsEvidenceRecord.created_at, RightsEvidenceRecord.id)
        )
        return list(values)

    async def readable_document_for_entry(
        self,
        entry: LibraryEntry,
    ) -> tuple[Document, Asset] | None:
        query = (
            select(Document, Asset)
            .join(Asset, Asset.id == Document.asset_id)
            .join(Edition, Edition.id == Asset.edition_id)
            .where(Edition.work_id == entry.work_id)
        )
        if entry.edition_id is not None:
            query = query.where(Edition.id == entry.edition_id)
        row = (
            await self._session.execute(
                query.order_by(Document.updated_at.desc(), Document.id.desc()).limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        document, asset = row
        return document, asset

    async def reading_state_for_entry(
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
