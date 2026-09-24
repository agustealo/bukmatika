from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import DocumentChunk, DocumentSection
from bukmatika.persistence.research import ResearchRepository, ResearchSearchMatch


class ResearchSemanticSelectionTooLarge(ValueError):
    code = "RESEARCH_SEMANTIC_SELECTION_TOO_LARGE"

    def __init__(self, *, candidate_count: int, max_chunks: int) -> None:
        super().__init__(
            f"Semantic selection contains {candidate_count} chunks; maximum is {max_chunks}"
        )
        self.candidate_count = candidate_count
        self.max_chunks = max_chunks


class SemanticResearchRepository:
    """Bounded canonical candidate projection for request-local semantic scoring."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def candidate_passages(
        self,
        *,
        principal_id: UUID,
        library_entry_ids: list[UUID],
        max_chunks: int,
    ) -> list[ResearchSearchMatch]:
        contexts = await ResearchRepository(self._session).document_contexts(
            principal_id=principal_id,
            library_entry_ids=library_entry_ids,
        )
        if not contexts:
            return []

        context_by_document = {context.document_id: context for context in contexts}
        candidate_count = int(
            await self._session.scalar(
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_id.in_(context_by_document)
                )
            )
            or 0
        )
        if candidate_count > max_chunks:
            raise ResearchSemanticSelectionTooLarge(
                candidate_count=candidate_count,
                max_chunks=max_chunks,
            )

        rows = (
            await self._session.execute(
                select(DocumentChunk, DocumentSection)
                .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
                .where(DocumentChunk.document_id.in_(context_by_document))
                .order_by(
                    DocumentChunk.document_id,
                    DocumentChunk.ordinal,
                    DocumentChunk.id,
                )
            )
        ).all()

        matches: list[ResearchSearchMatch] = []
        for chunk, section in rows:
            context = context_by_document.get(chunk.document_id)
            if context is None:
                raise RuntimeError("Semantic candidate references an unselected document")
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
                    score=0.0,
                )
            )
        return matches
