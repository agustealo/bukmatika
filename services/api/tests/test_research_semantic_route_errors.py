from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from bukmatika.ai.embedding_gateway import EmbeddingProviderNotReady
from bukmatika.ai.gateway import ModelProviderReadiness, ModelReadinessState
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.research.routes import research_semantic_search
from bukmatika.research.semantic import ResearchSemanticDisabled
from bukmatika.research.semantic_domain import ResearchSemanticSearchRequest


def _identity() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=uuid4(),
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _request() -> ResearchSemanticSearchRequest:
    return ResearchSemanticSearchRequest(
        query="semantic question",
        library_entry_ids=[uuid4()],
        limit=3,
    )


class _DisabledService:
    async def search(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise ResearchSemanticDisabled("Semantic retrieval is disabled for this principal")


class _MissingModelService:
    async def search(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise EmbeddingProviderNotReady(
            ModelProviderReadiness(
                state=ModelReadinessState.MODEL_MISSING,
                configured=True,
                ready=False,
                identity=None,
            )
        )


async def test_semantic_route_maps_disabled_to_conflict() -> None:
    with pytest.raises(HTTPException) as captured:
        await research_semantic_search(
            request=_request(),
            identity=_identity(),
            service=_DisabledService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == "Semantic retrieval is disabled for this principal"


async def test_semantic_route_maps_embedding_model_missing_to_unavailable() -> None:
    with pytest.raises(HTTPException) as captured:
        await research_semantic_search(
            request=_request(),
            identity=_identity(),
            service=_MissingModelService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 503
    assert captured.value.detail == "Embedding provider is not ready: model_missing"
