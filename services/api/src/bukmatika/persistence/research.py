from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Work


class ResearchSelectionDenied(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchDocumentContext:
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    edition_id: UUID
    edition_title: str
    asset_id: UUID
    document_id: UUID


@dataclass(frozen=True, slots=True)
class ResearchSearchMatch:
    context: ResearchDocumentContext
    chunk_id: UUID
    section_id: UUID
    section_ordinal: int
    chunk_ordinal: int
    heading: str | None
    locator: dict[str, Any]
    char_start: int
    char_end: int
    text: str
    score: float


class ResearchRepository:
    """Principal-scoped lexical retrieval over canonical owned documents."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_owned_passages(
        self,
        *,
        principal_id: UUID,
        library_entry_ids: list[UUID],
        query: str,
        limit: int,
    ) -> list[ResearchSearchMatch]:
        contexts = await self._document_contexts(
            principal_id=principal_id,
            library_entry_ids=library_entry_ids,
        )
        if not contexts:
            return []

        context_by_document = {context.document_id: context for context in contexts}
        configuration: ColumnElement[Any] = literal_column("'simple'::regconfig")
        tsquery = func.websearch_to_tsquery(configuration, query)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, tsquery).label("score")
        rows = (
            await self._session.execute(
                select(DocumentChunk, DocumentSection, rank)
                .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
                .where(
                    DocumentChunk.document_id.in_(context_by_document),
                    DocumentChunk.search_vector.op("@@")(tsquery),
                )
                .order_by(rank.desc(), DocumentChunk.document_id, DocumentChunk.ordinal)
                .limit(limit)
            )
        ).all()

        matches: list[ResearchSearchMatch] = []
        for chunk, section, score in rows:
            context = context_by_document.get(chunk.document_id)
            if context is None:
                raise RuntimeError("Research result references an unselected document")
            matches.append(
                ResearchSearchMatch(
                    context=context,
                    chunk_id=chunk.id,
                    section_id=section.id,
                    section_ordinal=section.ordinal,
                    chunk_ordinal=chunk.ordinal,
                    heading=section.heading,
                    locator=section.locator,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text=chunk.text,
                    score=float(score),
                )
            )
        return matches

    async def _document_contexts(
        self,
        *,
        principal_id: UUID,
        library_entry_ids: list[UUID],
    ) -> list[ResearchDocumentContext]:
        requested = set(library_entry_ids)
        owned = set(
            (
                await self._session.scalars(
                    select(LibraryEntry.id).where(
                        LibraryEntry.principal_id == principal_id,
                        LibraryEntry.id.in_(requested),
                    )
                )
            ).all()
        )
        if owned != requested:
            raise ResearchSelectionDenied("One or more selected library entries are unavailable")

        rows = (
            await self._session.execute(
                select(
                    LibraryEntry.id,
                    Work.id,
                    Work.canonical_title,
                    Edition.id,
                    Edition.title,
                    Asset.id,
                    Document.id,
                )
                .join(Work, Work.id == LibraryEntry.work_id)
                .join(
                    Edition,
                    and_(
                        Edition.work_id == LibraryEntry.work_id,
                        or_(
                            LibraryEntry.edition_id.is_(None),
                            LibraryEntry.edition_id == Edition.id,
                        ),
                    ),
                )
                .join(Asset, Asset.edition_id == Edition.id)
                .join(Document, Document.asset_id == Asset.id)
                .where(
                    LibraryEntry.principal_id == principal_id,
                    LibraryEntry.id.in_(requested),
                )
                .order_by(LibraryEntry.id, Edition.id, Document.id)
            )
        ).all()

        priority = {entry_id: index for index, entry_id in enumerate(library_entry_ids)}
        contexts_by_document: dict[UUID, ResearchDocumentContext] = {}
        for entry_id, work_id, work_title, edition_id, edition_title, asset_id, document_id in rows:
            candidate = ResearchDocumentContext(
                library_entry_id=entry_id,
                work_id=work_id,
                work_title=work_title,
                edition_id=edition_id,
                edition_title=edition_title,
                asset_id=asset_id,
                document_id=document_id,
            )
            existing = contexts_by_document.get(document_id)
            if existing is None or priority[entry_id] < priority[existing.library_entry_id]:
                contexts_by_document[document_id] = candidate
        return list(contexts_by_document.values())
