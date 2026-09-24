from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_research_lexical_fallback import _seed_book

from bukmatika.ai.embedding_gateway import EmbeddingBatch, EmbeddingRequest
from bukmatika.ai.gateway import (
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
)
from bukmatika.config import Settings
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.research import ResearchSelectionDenied
from bukmatika.persistence.semantic_research import ResearchSemanticSelectionTooLarge
from bukmatika.research.semantic import ResearchSemanticDisabled, SemanticResearchService
from bukmatika.research.semantic_domain import ResearchSemanticSearchRequest


class _SemanticProbeGateway:
    def __init__(self) -> None:
        self._identity = ModelProviderIdentity(
            provider="probe",
            model="semantic-probe-v1",
            routing="local",
        )
        self.readiness_calls = 0
        self.embedding_requests: list[list[str]] = []

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        self.readiness_calls += 1
        return ModelProviderReadiness(
            state=ModelReadinessState.READY,
            configured=True,
            ready=True,
            identity=self._identity,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        self.embedding_requests.append(list(request.inputs))
        vectors: list[list[float]] = []
        for text in request.inputs:
            normalized = text.casefold()
            if (
                "state authority exists" in normalized
                or "government by our wickedness" in normalized
            ):
                vectors.append([1.0, 0.0, 0.0])
            elif "necessary evil" in normalized:
                vectors.append([0.8, 0.2, 0.0])
            else:
                vectors.append([0.0, 1.0, 0.0])
        return EmbeddingBatch(identity=self._identity, vectors=vectors)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "embedding_provider": "ollama",
        "ollama_embedding_model": "embeddinggemma",
        "semantic_search_max_chunks": 256,
        "semantic_search_batch_size": 2,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


async def test_semantic_search_can_rank_no_overlap_canonical_passage_first(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="semantic-no-overlap")
    session.add(principal)
    await session.flush()
    entry, _, chunks = await _seed_book(
        session,
        principal=principal,
        suffix="semantic-no-overlap",
        chunks=[
            "Society is produced by our wants, and government by our wickedness.",
            "Government even in its best state is but a necessary evil.",
            "Agricultural ledgers tracked harvest yields and field boundaries.",
        ],
    )
    gateway = _SemanticProbeGateway()
    service = SemanticResearchService(
        gateway=gateway,
        settings=_settings(),
        session_scope_factory=_scope(session),
    )

    response = await service.search(
        principal_id=principal.id,
        request=ResearchSemanticSearchRequest(
            query="state authority exists because people are morally imperfect",
            library_entry_ids=[entry.id],
            limit=3,
        ),
    )

    assert response.candidate_count == 3
    assert response.passages[0].chunk_id == chunks[0].id
    assert response.passages[0].text.startswith("Society is produced")
    assert response.passages[0].score == pytest.approx(1.0)
    assert response.provider == "probe"
    assert response.model == "semantic-probe-v1"
    assert response.routing == "local"
    assert gateway.readiness_calls == 1
    assert gateway.embedding_requests[0] == [
        "state authority exists because people are morally imperfect"
    ]
    assert len(gateway.embedding_requests) == 3


async def test_semantic_search_ai_disabled_performs_zero_provider_calls(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="semantic-ai-disabled")
    session.add(principal)
    await session.flush()
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="semantic-ai-disabled",
        chunks=["Canonical source text."],
    )
    user_model = await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    user_model.ai_enabled = False
    await session.flush()

    gateway = _SemanticProbeGateway()
    service = SemanticResearchService(
        gateway=gateway,
        settings=_settings(),
        session_scope_factory=_scope(session),
    )

    with pytest.raises(ResearchSemanticDisabled):
        await service.search(
            principal_id=principal.id,
            request=ResearchSemanticSearchRequest(
                query="semantic question",
                library_entry_ids=[entry.id],
                limit=3,
            ),
        )

    assert gateway.readiness_calls == 0
    assert gateway.embedding_requests == []


async def test_semantic_search_model_disabled_performs_zero_provider_calls(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="semantic-model-disabled")
    session.add(principal)
    await session.flush()
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="semantic-model-disabled",
        chunks=["Canonical source text."],
    )
    user_model = await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    user_model.model_provider_override = "none"
    user_model.model_name_override = None
    await session.flush()

    gateway = _SemanticProbeGateway()
    service = SemanticResearchService(
        gateway=gateway,
        settings=_settings(),
        session_scope_factory=_scope(session),
    )

    with pytest.raises(ResearchSemanticDisabled):
        await service.search(
            principal_id=principal.id,
            request=ResearchSemanticSearchRequest(
                query="semantic question",
                library_entry_ids=[entry.id],
                limit=3,
            ),
        )

    assert gateway.readiness_calls == 0
    assert gateway.embedding_requests == []


async def test_semantic_search_oversized_selection_fails_before_provider(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="semantic-budget")
    session.add(principal)
    await session.flush()
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="semantic-budget",
        chunks=["First canonical chunk.", "Second canonical chunk."],
    )

    gateway = _SemanticProbeGateway()
    service = SemanticResearchService(
        gateway=gateway,
        settings=_settings(semantic_search_max_chunks=1),
        session_scope_factory=_scope(session),
    )

    with pytest.raises(ResearchSemanticSelectionTooLarge) as exc_info:
        await service.search(
            principal_id=principal.id,
            request=ResearchSemanticSearchRequest(
                query="semantic question",
                library_entry_ids=[entry.id],
                limit=3,
            ),
        )

    assert exc_info.value.candidate_count == 2
    assert exc_info.value.max_chunks == 1
    assert gateway.readiness_calls == 0
    assert gateway.embedding_requests == []


async def test_semantic_search_cross_principal_selection_never_reaches_provider(
    session: AsyncSession,
) -> None:
    owner = Principal(kind="local", external_subject="semantic-owner")
    attacker = Principal(kind="local", external_subject="semantic-attacker")
    session.add_all([owner, attacker])
    await session.flush()
    entry, _, _ = await _seed_book(
        session,
        principal=owner,
        suffix="semantic-owned",
        chunks=["Owner-only canonical source text."],
    )

    gateway = _SemanticProbeGateway()
    service = SemanticResearchService(
        gateway=gateway,
        settings=_settings(),
        session_scope_factory=_scope(session),
    )

    with pytest.raises(ResearchSelectionDenied):
        await service.search(
            principal_id=attacker.id,
            request=ResearchSemanticSearchRequest(
                query="semantic question",
                library_entry_ids=[entry.id],
                limit=3,
            ),
        )

    assert gateway.readiness_calls == 0
    assert gateway.embedding_requests == []
