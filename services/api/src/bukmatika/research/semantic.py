import math
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import replace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingProviderNotReady,
    EmbeddingProviderResponseInvalid,
    EmbeddingRequest,
)
from bukmatika.ai.gateway import ModelDataClassification
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.research import ResearchSearchMatch
from bukmatika.persistence.semantic_research import SemanticResearchRepository
from bukmatika.research.domain import ResearchPassageResponse
from bukmatika.research.semantic_domain import (
    ResearchSemanticSearchRequest,
    ResearchSemanticSearchResponse,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ResearchSemanticDisabled(RuntimeError):
    code = "RESEARCH_SEMANTIC_DISABLED"


class ResearchSemanticIntegrityError(RuntimeError):
    code = "RESEARCH_SEMANTIC_INTEGRITY_ERROR"


class SemanticResearchService:
    """Explicit, bounded semantic retrieval over canonical principal-owned chunks."""

    def __init__(
        self,
        *,
        gateway: EmbeddingGateway,
        settings: Settings,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._gateway = gateway
        self._settings = settings
        self._session_scope = session_scope_factory

    async def search(
        self,
        *,
        principal_id: UUID,
        request: ResearchSemanticSearchRequest,
    ) -> ResearchSemanticSearchResponse:
        async with self._session_scope() as database_session:
            user_model = await PersonalizationRepository(database_session).get_or_create_user_model(
                principal_id
            )
            if not user_model.ai_enabled or user_model.model_provider_override == "none":
                raise ResearchSemanticDisabled("Semantic retrieval is disabled for this principal")

            candidates = await SemanticResearchRepository(database_session).candidate_passages(
                principal_id=principal_id,
                library_entry_ids=request.library_entry_ids,
                max_chunks=self._settings.semantic_search_max_chunks,
            )

        readiness = await self._gateway.readiness()
        if not readiness.ready or readiness.identity is None:
            raise EmbeddingProviderNotReady(readiness)

        ranked = await self._rank(query=request.query, candidates=candidates)
        selected = ranked[: request.limit]

        async with self._session_scope() as database_session:
            await InteractionEventRepository(database_session).record(
                SemanticEventType.RESEARCH_SEMANTIC_SEARCH_COMPLETED,
                principal_id=principal_id,
                entity_type="library_selection",
                context={
                    "query": request.query,
                    "selected_library_entry_ids": [
                        str(entry_id) for entry_id in request.library_entry_ids
                    ],
                    "candidate_count": len(candidates),
                    "result_count": len(selected),
                    "provider": readiness.identity.provider,
                    "model": readiness.identity.model,
                    "routing": readiness.identity.routing,
                },
            )

        return ResearchSemanticSearchResponse(
            query=request.query,
            selected_library_entry_ids=request.library_entry_ids,
            passages=[_passage_response(match) for match in selected],
            candidate_count=len(candidates),
            provider=readiness.identity.provider,
            model=readiness.identity.model,
            routing=readiness.identity.routing,
        )

    async def _rank(
        self,
        *,
        query: str,
        candidates: list[ResearchSearchMatch],
    ) -> list[ResearchSearchMatch]:
        if not candidates:
            return []

        query_batch = await self._gateway.embed(
            EmbeddingRequest(
                inputs=[query],
                data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
                timeout_seconds=self._settings.embedding_timeout_seconds,
            )
        )
        if len(query_batch.vectors) != 1:
            raise EmbeddingProviderResponseInvalid(
                "Embedding provider returned an invalid query vector count"
            )
        query_vector = query_batch.vectors[0]

        scored: list[ResearchSearchMatch] = []
        batch_size = self._settings.semantic_search_batch_size
        for offset in range(0, len(candidates), batch_size):
            batch_candidates = candidates[offset : offset + batch_size]
            batch = await self._gateway.embed(
                EmbeddingRequest(
                    inputs=[candidate.text for candidate in batch_candidates],
                    data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
                    timeout_seconds=self._settings.embedding_timeout_seconds,
                )
            )
            if batch.identity != query_batch.identity:
                raise ResearchSemanticIntegrityError(
                    "Embedding provider identity changed within one semantic request"
                )
            if len(batch.vectors) != len(batch_candidates):
                raise EmbeddingProviderResponseInvalid(
                    "Embedding provider returned an invalid candidate vector count"
                )
            for candidate, vector in zip(batch_candidates, batch.vectors, strict=True):
                score = _cosine_similarity(query_vector, vector)
                scored.append(replace(candidate, score=score))

        scored.sort(
            key=lambda match: (
                -match.score,
                str(match.context.document_id),
                match.chunk_ordinal,
                str(match.chunk_id),
            )
        )
        return scored


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        raise ResearchSemanticIntegrityError("Embedding dimensions do not match")
    if any(not math.isfinite(value) for value in left + right):
        raise ResearchSemanticIntegrityError("Embedding vector contains a non-finite value")

    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ResearchSemanticIntegrityError("Embedding vector has zero magnitude")
    similarity = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return max(-1.0, min(1.0, similarity))


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
