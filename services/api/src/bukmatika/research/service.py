import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.research import (
    ResearchDocumentContext,
    ResearchReaderPositionInvalid,
    ResearchRepository,
    ResearchSearchMatch,
    ResearchSelectionDenied,
)
from bukmatika.research.domain import (
    MAX_RESEARCH_EVIDENCE_ITEMS,
    GroundedResearchAnswer,
    ResearchCompareRequest,
    ResearchCompareResponse,
    ResearchComparisonEdition,
    ResearchComparisonSource,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceBundleResponse,
    ResearchEvidenceItem,
    ResearchEvidenceSourceKind,
    ResearchPassageResponse,
    ResearchSearchRequest,
    ResearchSearchResponse,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_GROUNDING_STOP_WORDS = frozenset(
    {
        "about",
        "book",
        "chapter",
        "could",
        "discuss",
        "discusses",
        "does",
        "explain",
        "from",
        "have",
        "how",
        "passage",
        "said",
        "says",
        "show",
        "shows",
        "source",
        "text",
        "that",
        "this",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "would",
    }
)


class ResearchEvidenceReferenceInvalid(ValueError):
    code = "RESEARCH_EVIDENCE_REFERENCE_INVALID"


class ResearchService:
    """Grounded retrieval and evidence assembly over explicitly selected owned books."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def search(
        self,
        *,
        principal_id: UUID,
        request: ResearchSearchRequest,
    ) -> ResearchSearchResponse:
        async with self._session_scope() as database_session:
            repository = ResearchRepository(database_session)
            matches = await repository.search_owned_passages(
                principal_id=principal_id,
                library_entry_ids=request.library_entry_ids,
                query=request.query,
                limit=request.limit,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.RESEARCH_SEARCH_COMPLETED,
                principal_id=principal_id,
                entity_type="library_selection",
                context={
                    "query": request.query,
                    "selected_library_entry_ids": [
                        str(entry_id) for entry_id in request.library_entry_ids
                    ],
                    "result_count": len(matches),
                },
            )
            return ResearchSearchResponse(
                query=request.query,
                selected_library_entry_ids=request.library_entry_ids,
                passages=[_passage_response(match) for match in matches],
            )

    async def compare(
        self,
        *,
        principal_id: UUID,
        request: ResearchCompareRequest,
    ) -> ResearchCompareResponse:
        async with self._session_scope() as database_session:
            repository = ResearchRepository(database_session)
            sources: list[ResearchComparisonSource] = []
            passage_count = 0
            matched_source_count = 0

            for entry_id in request.library_entry_ids:
                entry_contexts = await repository.document_contexts(
                    principal_id=principal_id,
                    library_entry_ids=[entry_id],
                )
                if not entry_contexts:
                    raise ResearchSelectionDenied(
                        "Selected comparison source has no processed research document"
                    )

                matches = await repository.search_owned_passages(
                    principal_id=principal_id,
                    library_entry_ids=[entry_id],
                    query=request.query,
                    limit=request.per_source_limit,
                )
                if matches:
                    matched_source_count += 1
                passage_count += len(matches)
                first = entry_contexts[0]
                sources.append(
                    ResearchComparisonSource(
                        library_entry_id=entry_id,
                        work_id=first.work_id,
                        work_title=first.work_title,
                        available_editions=_comparison_editions(entry_contexts),
                        passages=[_passage_response(match) for match in matches],
                    )
                )

            await InteractionEventRepository(database_session).record(
                SemanticEventType.RESEARCH_COMPARISON_COMPLETED,
                principal_id=principal_id,
                entity_type="library_selection",
                context={
                    "query": request.query,
                    "selected_library_entry_ids": [
                        str(entry_id) for entry_id in request.library_entry_ids
                    ],
                    "source_count": len(sources),
                    "matched_source_count": matched_source_count,
                    "passage_count": passage_count,
                    "per_source_limit": request.per_source_limit,
                },
            )
            return ResearchCompareResponse(
                query=request.query,
                selected_library_entry_ids=request.library_entry_ids,
                per_source_limit=request.per_source_limit,
                sources=sources,
            )

    async def evidence_bundle(
        self,
        *,
        principal_id: UUID,
        request: ResearchEvidenceBundleRequest,
    ) -> ResearchEvidenceBundleResponse:
        async with self._session_scope() as database_session:
            repository = ResearchRepository(database_session)
            reader_matches = await repository.reader_passages(
                principal_id=principal_id,
                library_entry_id=request.reader.library_entry_id,
                document_id=request.reader.document_id,
                section_id=request.reader.section_id,
                char_offset=request.reader.char_offset,
                selection_start=request.reader.selection_start,
                selection_end=request.reader.selection_end,
            )
            highlight_matches = await repository.highlight_passages(
                principal_id=principal_id,
                library_entry_ids=request.library_entry_ids,
                highlight_ids=request.highlight_ids,
            )
            reserved_count = len(reader_matches) + len(highlight_matches)
            if reserved_count > MAX_RESEARCH_EVIDENCE_ITEMS:
                raise ResearchReaderPositionInvalid(
                    "Reader and selected highlights exceed the evidence bundle budget"
                )
            related_limit = min(
                request.related_limit,
                MAX_RESEARCH_EVIDENCE_ITEMS - reserved_count,
            )
            related_matches = (
                await repository.search_owned_passages(
                    principal_id=principal_id,
                    library_entry_ids=request.library_entry_ids,
                    query=_grounding_search_query(request.question),
                    limit=related_limit,
                )
                if related_limit > 0
                else []
            )

            evidence: list[ResearchEvidenceItem] = []
            covered_chunks: set[UUID] = set()
            reader_kind = (
                ResearchEvidenceSourceKind.READER_SELECTION
                if request.reader.selection_start is not None
                else ResearchEvidenceSourceKind.READER_POSITION
            )
            for match in reader_matches:
                if match.chunk_id in covered_chunks:
                    continue
                covered_chunks.add(match.chunk_id)
                evidence.append(
                    _evidence_item(
                        match,
                        evidence_id=f"E{len(evidence) + 1}",
                        source_kind=reader_kind,
                        score=None,
                    )
                )
            for match in highlight_matches:
                covered_chunks.add(match.chunk_id)
                evidence.append(
                    _evidence_item(
                        match,
                        evidence_id=f"E{len(evidence) + 1}",
                        source_kind=ResearchEvidenceSourceKind.HIGHLIGHT_SELECTION,
                        score=None,
                    )
                )
            for match in related_matches:
                if match.chunk_id in covered_chunks:
                    continue
                covered_chunks.add(match.chunk_id)
                evidence.append(
                    _evidence_item(
                        match,
                        evidence_id=f"E{len(evidence) + 1}",
                        source_kind=ResearchEvidenceSourceKind.RELATED_PASSAGE,
                        score=max(0.0, match.score),
                    )
                )

            if not evidence:
                raise ResearchReaderPositionInvalid("Reader context produced no canonical evidence")

            await InteractionEventRepository(database_session).record(
                SemanticEventType.RESEARCH_EVIDENCE_BUILT,
                principal_id=principal_id,
                entity_type="document",
                entity_id=request.reader.document_id,
                context={
                    "library_entry_id": str(request.reader.library_entry_id),
                    "section_id": str(request.reader.section_id),
                    "selected_library_entry_ids": [
                        str(entry_id) for entry_id in request.library_entry_ids
                    ],
                    "selected_highlight_ids": [
                        str(highlight_id) for highlight_id in request.highlight_ids
                    ],
                    "reader_evidence_count": len(reader_matches),
                    "highlight_evidence_count": len(highlight_matches),
                    "evidence_count": len(evidence),
                },
            )
            return ResearchEvidenceBundleResponse(
                question=request.question,
                reader=request.reader,
                selected_library_entry_ids=request.library_entry_ids,
                evidence=evidence,
            )

    @staticmethod
    def validate_grounded_answer(
        *,
        bundle: ResearchEvidenceBundleResponse,
        answer: GroundedResearchAnswer,
    ) -> GroundedResearchAnswer:
        allowed = {item.evidence_id for item in bundle.evidence}
        for claim in answer.claims:
            unknown = sorted(set(claim.evidence_ids) - allowed)
            if unknown:
                raise ResearchEvidenceReferenceInvalid(
                    "Grounded answer references unknown evidence IDs: " + ", ".join(unknown)
                )
        return answer


def _comparison_editions(
    contexts: list[ResearchDocumentContext],
) -> list[ResearchComparisonEdition]:
    editions: list[ResearchComparisonEdition] = []
    seen: set[UUID] = set()
    for context in contexts:
        if context.edition_id in seen:
            continue
        seen.add(context.edition_id)
        editions.append(
            ResearchComparisonEdition(
                edition_id=context.edition_id,
                edition_title=context.edition_title,
            )
        )
    return editions


def _grounding_search_query(question: str) -> str:
    tokens = re.findall(r"[\w'-]{3,32}", question.casefold())
    selected: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in _GROUNDING_STOP_WORDS or token in seen:
            continue
        seen.add(token)
        selected.append(token)
        if len(selected) == 5:
            break
    if not selected:
        return " ".join(question.split())
    return " OR ".join(selected)


def _passage_response(match: ResearchSearchMatch) -> ResearchPassageResponse:
    return ResearchPassageResponse(
        library_entry_id=match.context.library_entry_id,
        work_id=match.context.work_id,
        work_title=match.context.work_title,
        edition_id=match.context.edition_id,
        edition_title=match.context.edition_title,
        asset_id=match.context.asset_id,
        document_id=match.context.document_id,
        chunk_id=match.chunk_id,
        section_id=match.section_id,
        section_ordinal=match.section_ordinal,
        chunk_ordinal=match.chunk_ordinal,
        heading=match.heading,
        locator=match.locator,
        char_start=match.char_start,
        char_end=match.char_end,
        text=match.text,
        score=max(0.0, match.score),
    )


def _evidence_item(
    match: ResearchSearchMatch,
    *,
    evidence_id: str,
    source_kind: ResearchEvidenceSourceKind,
    score: float | None,
) -> ResearchEvidenceItem:
    return ResearchEvidenceItem(
        evidence_id=evidence_id,
        source_kind=source_kind,
        highlight_id=match.highlight_id,
        library_entry_id=match.context.library_entry_id,
        work_id=match.context.work_id,
        work_title=match.context.work_title,
        edition_id=match.context.edition_id,
        edition_title=match.context.edition_title,
        asset_id=match.context.asset_id,
        document_id=match.context.document_id,
        chunk_id=match.chunk_id,
        section_id=match.section_id,
        section_ordinal=match.section_ordinal,
        chunk_ordinal=match.chunk_ordinal,
        heading=match.heading,
        locator=match.locator,
        char_start=match.char_start,
        char_end=match.char_end,
        text=match.text,
        score=score,
    )


__all__ = [
    "ResearchEvidenceReferenceInvalid",
    "ResearchReaderPositionInvalid",
    "ResearchSelectionDenied",
    "ResearchService",
]
