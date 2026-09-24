from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient
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
from bukmatika.main import app
from bukmatika.persistence.models import Principal
from bukmatika.research.routes import semantic_research_service
from bukmatika.research.semantic import SemanticResearchService


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
            [1.0, 0.0] if "moral" in value.casefold() or "wickedness" in value.casefold() else [0.0, 1.0]
            for value in request.inputs
        ]
        return EmbeddingBatch(identity=self._identity, vectors=vectors)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_semantic_route_returns_canonical_ranked_passage(
    session: AsyncSession,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
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

    async def identity_override() -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(principal_id=principal.id)

    async def semantic_override() -> AsyncIterator[SemanticResearchService]:
        yield SemanticResearchService(
            gateway=_RouteProbeGateway(),
            settings=Settings(
                embedding_provider="ollama",
                ollama_embedding_model="embeddinggemma",
                semantic_search_batch_size=2,
            ),
            session_scope_factory=_scope(session),
        )

    from bukmatika.identity import require_principal

    app.dependency_overrides[require_principal] = identity_override
    app.dependency_overrides[semantic_research_service] = semantic_override
    monkeypatch.setattr("bukmatika.main.settings.acquisition_worker_enabled", False)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/research/semantic-search",
                json={
                    "query": "state authority exists because people are morally imperfect",
                    "library_entry_ids": [str(entry.id)],
                    "limit": 2,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_count"] == 2
    assert payload["provider"] == "probe"
    assert payload["model"] == "route-probe-v1"
    assert payload["routing"] == "local"
    assert payload["passages"][0]["chunk_id"] == str(chunks[0].id)
    assert payload["passages"][0]["char_start"] == chunks[0].char_start
    assert payload["passages"][0]["char_end"] == chunks[0].char_end
