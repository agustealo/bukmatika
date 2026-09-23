from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    Identifier,
    LibraryEntry,
    RightsDecision,
    RightsDecisionEvidence,
    RightsEvidenceRecord,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)
from bukmatika.persistence.reader_models import Bookmark, Highlight, ReadingState


@dataclass(frozen=True, slots=True)
class SourceReferenceRecord:
    source: SourceRecord
    relationship: str


@dataclass(frozen=True, slots=True)
class AssetPortabilityRecord:
    asset: Asset
    edition: Edition
    document: Document | None
    stored_object: StoredObject | None
    rights_decision: RightsDecision | None
    rights_evidence: tuple[RightsEvidenceRecord, ...]


@dataclass(frozen=True, slots=True)
class BookmarkPortabilityRecord:
    bookmark: Bookmark
    section_ordinal: int


@dataclass(frozen=True, slots=True)
class HighlightPortabilityRecord:
    highlight: Highlight
    section_ordinal: int


@dataclass(frozen=True, slots=True)
class ReadingStatePortabilityRecord:
    state: ReadingState
    document: Document
    bookmarks: tuple[BookmarkPortabilityRecord, ...]
    highlights: tuple[HighlightPortabilityRecord, ...]


class LibraryPortabilityRepository:
    """Read-only projection of canonical library state for portable manifests."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def entries(self, principal_id: UUID) -> list[LibraryEntry]:
        values = await self._session.scalars(
            select(LibraryEntry)
            .join(Work, Work.id == LibraryEntry.work_id)
            .outerjoin(Edition, Edition.id == LibraryEntry.edition_id)
            .where(LibraryEntry.principal_id == principal_id)
            .order_by(
                Work.normalized_title,
                Edition.title.asc().nullsfirst(),
                LibraryEntry.id,
            )
        )
        return list(values)

    async def work(self, work_id: UUID) -> Work:
        work = await self._session.get(Work, work_id)
        if work is None:
            raise RuntimeError("Library entry references missing work")
        return work

    async def edition(self, edition_id: UUID) -> Edition:
        edition = await self._session.get(Edition, edition_id)
        if edition is None:
            raise RuntimeError("Library entry references missing edition")
        return edition

    async def authors(self, work_id: UUID) -> list[str]:
        values = await self._session.scalars(
            select(Contributor.display_name)
            .join(WorkContributor, WorkContributor.contributor_id == Contributor.id)
            .where(
                WorkContributor.work_id == work_id,
                WorkContributor.role == "author",
            )
            .order_by(Contributor.normalized_name, Contributor.id)
        )
        return list(values)

    async def subjects(self, work_id: UUID) -> list[str]:
        values = await self._session.scalars(
            select(Subject.display_name)
            .join(WorkSubject, WorkSubject.subject_id == Subject.id)
            .where(WorkSubject.work_id == work_id)
            .order_by(Subject.normalized_name, Subject.id)
        )
        return list(values)

    async def identifiers(self, entity_type: str, entity_id: UUID) -> list[Identifier]:
        values = await self._session.scalars(
            select(Identifier)
            .where(
                Identifier.entity_type == entity_type,
                Identifier.entity_id == entity_id,
            )
            .order_by(Identifier.scheme, Identifier.normalized_value, Identifier.id)
        )
        return list(values)

    async def source_references(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> list[SourceReferenceRecord]:
        rows = (
            await self._session.execute(
                select(SourceRecord, SourceRecordLink.relationship)
                .join(
                    SourceRecordLink,
                    SourceRecordLink.source_record_id == SourceRecord.id,
                )
                .where(
                    SourceRecordLink.entity_type == entity_type,
                    SourceRecordLink.entity_id == entity_id,
                )
                .order_by(
                    SourceRecord.provider,
                    SourceRecord.provider_record_id,
                    SourceRecordLink.relationship,
                    SourceRecord.id,
                )
            )
        ).all()
        return [
            SourceReferenceRecord(source=source, relationship=relationship)
            for source, relationship in rows
        ]

    async def asset_records(self, entry: LibraryEntry) -> list[AssetPortabilityRecord]:
        query = (
            select(Asset, Edition)
            .join(Edition, Edition.id == Asset.edition_id)
            .where(Edition.work_id == entry.work_id)
        )
        if entry.edition_id is not None:
            query = query.where(Edition.id == entry.edition_id)
        rows = (
            await self._session.execute(
                query.order_by(
                    Edition.publication_year.asc().nullsfirst(),
                    Edition.title,
                    Asset.format,
                    Asset.id,
                )
            )
        ).all()
        records: list[AssetPortabilityRecord] = []
        for asset, edition in rows:
            document = await self._session.scalar(
                select(Document).where(Document.asset_id == asset.id)
            )
            stored_object = (
                await self._session.get(StoredObject, asset.stored_object_id)
                if asset.stored_object_id is not None
                else None
            )
            rights_decision = await self._session.scalar(
                select(RightsDecision)
                .where(
                    RightsDecision.subject_type == "asset",
                    RightsDecision.subject_id == asset.id,
                )
                .order_by(RightsDecision.evaluated_at.desc(), RightsDecision.id.desc())
                .limit(1)
            )
            evidence: tuple[RightsEvidenceRecord, ...] = ()
            if rights_decision is not None:
                evidence = tuple(
                    (
                        await self._session.scalars(
                            select(RightsEvidenceRecord)
                            .join(
                                RightsDecisionEvidence,
                                RightsDecisionEvidence.rights_evidence_id
                                == RightsEvidenceRecord.id,
                            )
                            .where(
                                RightsDecisionEvidence.rights_decision_id
                                == rights_decision.id
                            )
                            .order_by(RightsEvidenceRecord.created_at, RightsEvidenceRecord.id)
                        )
                    ).all()
                )
            records.append(
                AssetPortabilityRecord(
                    asset=asset,
                    edition=edition,
                    document=document,
                    stored_object=stored_object,
                    rights_decision=rights_decision,
                    rights_evidence=evidence,
                )
            )
        return records

    async def reading_states(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
    ) -> list[ReadingStatePortabilityRecord]:
        rows = (
            await self._session.execute(
                select(ReadingState, Document)
                .join(Document, Document.id == ReadingState.document_id)
                .join(LibraryEntry, LibraryEntry.id == ReadingState.library_entry_id)
                .where(
                    ReadingState.library_entry_id == library_entry_id,
                    LibraryEntry.principal_id == principal_id,
                )
                .order_by(Document.source_sha256, Document.id, ReadingState.id)
            )
        ).all()
        result: list[ReadingStatePortabilityRecord] = []
        for state, document in rows:
            bookmark_rows = (
                await self._session.execute(
                    select(Bookmark, DocumentSection.ordinal)
                    .join(DocumentSection, DocumentSection.id == Bookmark.section_id)
                    .where(Bookmark.reading_state_id == state.id)
                    .order_by(
                        DocumentSection.ordinal,
                        Bookmark.char_offset,
                        Bookmark.created_at,
                        Bookmark.id,
                    )
                )
            ).all()
            highlight_rows = (
                await self._session.execute(
                    select(Highlight, DocumentSection.ordinal)
                    .join(DocumentSection, DocumentSection.id == Highlight.section_id)
                    .where(Highlight.reading_state_id == state.id)
                    .order_by(
                        DocumentSection.ordinal,
                        Highlight.char_start,
                        Highlight.char_end,
                        Highlight.created_at,
                        Highlight.id,
                    )
                )
            ).all()
            result.append(
                ReadingStatePortabilityRecord(
                    state=state,
                    document=document,
                    bookmarks=tuple(
                        BookmarkPortabilityRecord(
                            bookmark=bookmark,
                            section_ordinal=section_ordinal,
                        )
                        for bookmark, section_ordinal in bookmark_rows
                    ),
                    highlights=tuple(
                        HighlightPortabilityRecord(
                            highlight=highlight,
                            section_ordinal=section_ordinal,
                        )
                        for highlight, section_ordinal in highlight_rows
                    ),
                )
            )
        return result
