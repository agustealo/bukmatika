from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from test_research_lexical_fallback import _seed_book

from bukmatika.ai.embedding_gateway import EmbeddingBatch, EmbeddingRequest
from bukmatika.ai.gateway import (
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
)
from bukmatika.config import Settings
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.models import Principal
from bukmatika.research.routes import research_semantic_search
from bukmatika.research.semantic import SemanticResearchService
from bukmatika.research.semantic_domain import ResearchSemanticSearchRequest


class _RouteProbeGateway:
    def __init__(self) -> None:
        self._identity = ModelProviderIdentity(
            provider="probe",
            model="route-probe-v1",
            routing="local",
        )

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        return ModelProviderReadiness(
            state=ModelReadinessState.READY,
            configured=True,
            ready=True,
            identity=self._identity,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        vectors = [
            [1.0, 0.0]
            if "moral" in value.casefold() or "wickedness" in value.casefold()
            else [0.0, 1.0]
            for value in request.inputs
        ]
        return EmbeddingBatch(identity=self._identity, vectors=vectors)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_semantic_route_returns_canonical_ranked_passage(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="semantic-route")
    session.add(principal)
    await session.flush()
    entry, _, chunks = await _seed_book(
        session,
        principal=principal,
        suffix="semantic-route",
        chunks=[
            "Society is produced by our wants, and government by our wickedness.",
            "Agricultural ledgers tracked harvest yields.",
        ],
    )
    identity = AuthenticatedPrincipal(
        principal_id=principal.id,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service = SemanticResearchService(
        gateway=_RouteProbeGateway(),
        settings=Settings(
            embedding_provider="ollama",
            ollama_embedding_model="embeddinggemma",
            semantic_search_batch_size=2,
        ),
        session_scope_factory=_scope(session),
    )

    response = await research_semantic_search(
        request=ResearchSemanticSearchRequest(
            query="state authority exists because people are morally imperfect",
            library_entry_ids=[entry.id],
            limit=2,
        ),
        identity=identity,
        service=service,
    )

    assert response.candidate_count == 2
    assert response.provider == "probe"
    assert response.model == "route-probe-v1"
    assert response.routing == "local"
    assert response.passages[0].chunk_id == chunks[0].id
    assert response.passages[0].char_start == chunks[0].char_start
    assert response.passages[0].char_end == chunks[0].char_end
