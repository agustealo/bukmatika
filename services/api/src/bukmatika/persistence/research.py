from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Work
from bukmatika.persistence.reader_models import Highlight, ReadingState

MAX_READER_SELECTION_CHUNKS = 8


class ResearchSelectionDenied(LookupError):
    pass


class ResearchReaderPositionInvalid(ValueError):
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
    source_highlight_id: UUID | None = None


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
        contexts = await self.document_contexts(
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

    async def reader_passages(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        section_id: UUID,
        char_offset: int,
        selection_start: int | None,
        selection_end: int | None,
    ) -> list[ResearchSearchMatch]:
        contexts = await self.document_contexts(
            principal_id=principal_id,
            library_entry_ids=[library_entry_id],
        )
        context = next(
            (candidate for candidate in contexts if candidate.document_id == document_id),
            None,
        )
        if context is None:
            raise ResearchSelectionDenied("Reader document is unavailable to this principal")

        section = await self._session.scalar(
            select(DocumentSection).where(
                DocumentSection.id == section_id,
                DocumentSection.document_id == document_id,
            )
        )
        if section is None:
            raise ResearchReaderPositionInvalid("Reader section is outside the selected document")
        if char_offset < 0 or char_offset > len(section.text):
            raise ResearchReaderPositionInvalid("Reader character offset is outside the section")

        if selection_start is not None and selection_end is not None:
            if selection_start < 0 or selection_end > len(section.text):
                raise ResearchReaderPositionInvalid("Reader selection is outside the section text")
            if selection_start >= selection_end:
                raise ResearchReaderPositionInvalid("Reader selection must have positive length")
            chunks = list(
                (
                    await self._session.scalars(
                        select(DocumentChunk)
                        .where(
                            DocumentChunk.document_id == document_id,
                            DocumentChunk.section_id == section_id,
                            DocumentChunk.char_start < selection_end,
                            DocumentChunk.char_end > selection_start,
                        )
                        .order_by(DocumentChunk.ordinal)
                    )
                ).all()
            )
        else:
            chunks = list(
                (
                    await self._session.scalars(
                        select(DocumentChunk)
                        .where(
                            DocumentChunk.document_id == document_id,
                            DocumentChunk.section_id == section_id,
                            DocumentChunk.char_start <= char_offset,
                            DocumentChunk.char_end > char_offset,
                        )
                        .order_by(DocumentChunk.ordinal)
                        .limit(1)
                    )
                ).all()
            )
            if not chunks and char_offset == len(section.text):
                last_chunk = await self._session.scalar(
                    select(DocumentChunk)
                    .where(
                        DocumentChunk.document_id == document_id,
                        DocumentChunk.section_id == section_id,
                    )
                    .order_by(DocumentChunk.ordinal.desc())
                    .limit(1)
                )
                if last_chunk is not None:
                    chunks = [last_chunk]

        if not chunks:
            raise ResearchReaderPositionInvalid("Reader position has no canonical text chunk")
        if len(chunks) > MAX_READER_SELECTION_CHUNKS:
            raise ResearchReaderPositionInvalid("Reader selection spans too many evidence chunks")

        matches: list[ResearchSearchMatch] = []
        for chunk in chunks:
            start = chunk.char_start
            end = chunk.char_end
            text_value = chunk.text
            if selection_start is not None and selection_end is not None:
                start = max(start, selection_start)
                end = min(end, selection_end)
                relative_start = max(0, start - chunk.char_start)
                relative_end = max(relative_start, end - chunk.char_start)
                text_value = chunk.text[relative_start:relative_end]
            matches.append(
                ResearchSearchMatch(
                    context=context,
                    chunk_id=chunk.id,
                    section_id=section.id,
                    section_ordinal=section.ordinal,
                    chunk_ordinal=chunk.ordinal,
                    heading=section.heading,
                    locator=section.locator,
                    char_start=start,
                    char_end=end,
                    text=text_value,
                    score=0.0,
                )
            )
        return matches

    async def selected_highlight_passages(
        self,
        *,
        principal_id: UUID,
        library_entry_ids: list[UUID],
        highlight_ids: list[UUID],
    ) -> list[ResearchSearchMatch]:
        if not highlight_ids:
            return []

        requested = set(highlight_ids)
        rows = (
            await self._session.execute(
                select(
                    Highlight,
                    ReadingState.library_entry_id,
                    DocumentSection,
                    Work.id,
                    Work.canonical_title,
                    Edition.id,
                    Edition.title,
                    Asset.id,
                    Document.id,
                )
                .join(ReadingState, ReadingState.id == Highlight.reading_state_id)
                .join(LibraryEntry, LibraryEntry.id == ReadingState.library_entry_id)
                .join(Document, Document.id == ReadingState.document_id)
                .join(Asset, Asset.id == Document.asset_id)
                .join(Edition, Edition.id == Asset.edition_id)
                .join(Work, Work.id == Edition.work_id)
                .join(DocumentSection, DocumentSection.id == Highlight.section_id)
                .where(
                    Highlight.id.in_(requested),
                    LibraryEntry.principal_id == principal_id,
                    LibraryEntry.id.in_(library_entry_ids),
                    Edition.work_id == LibraryEntry.work_id,
                    or_(
                        LibraryEntry.edition_id.is_(None),
                        LibraryEntry.edition_id == Edition.id,
                    ),
                    DocumentSection.document_id == ReadingState.document_id,
                )
            )
        ).all()
        row_by_highlight = {row[0].id: row for row in rows}
        if set(row_by_highlight) != requested:
            raise ResearchSelectionDenied("One or more selected highlights are unavailable")

        matches: list[ResearchSearchMatch] = []
        for highlight_id in highlight_ids:
            (
                highlight,
                library_entry_id,
                section,
                work_id,
                work_title,
                edition_id,
                edition_title,
                asset_id,
                document_id,
            ) = row_by_highlight[highlight_id]
            if (
                highlight.char_start < 0
                or highlight.char_start >= highlight.char_end
                or highlight.char_end > len(section.text)
            ):
                raise ResearchReaderPositionInvalid(
                    "Selected highlight coordinates are outside canonical section text"
                )

            chunks = list(
                (
                    await self._session.scalars(
                        select(DocumentChunk)
                        .where(
                            DocumentChunk.document_id == document_id,
                            DocumentChunk.section_id == section.id,
                            DocumentChunk.char_start < highlight.char_end,
                            DocumentChunk.char_end > highlight.char_start,
                        )
                        .order_by(DocumentChunk.ordinal)
                    )
                ).all()
            )
            if not chunks:
                raise ResearchReaderPositionInvalid(
                    "Selected highlight has no canonical evidence chunk"
                )
            if len(chunks) > MAX_READER_SELECTION_CHUNKS:
                raise ResearchReaderPositionInvalid(
                    "Selected highlight spans too many evidence chunks"
                )

            context = ResearchDocumentContext(
                library_entry_id=library_entry_id,
                work_id=work_id,
                work_title=work_title,
                edition_id=edition_id,
                edition_title=edition_title,
                asset_id=asset_id,
                document_id=document_id,
            )
            for chunk in chunks:
                start = max(chunk.char_start, highlight.char_start)
                end = min(chunk.char_end, highlight.char_end)
                matches.append(
                    ResearchSearchMatch(
                        context=context,
                        chunk_id=chunk.id,
                        section_id=section.id,
                        section_ordinal=section.ordinal,
                        chunk_ordinal=chunk.ordinal,
                        heading=section.heading,
                        locator=section.locator,
                        char_start=start,
                        char_end=end,
                        text=section.text[start:end],
                        score=0.0,
                        source_highlight_id=highlight.id,
                    )
                )
        return matches

    async def document_contexts(
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
