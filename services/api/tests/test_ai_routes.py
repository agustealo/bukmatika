from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from bukmatika.ai.gateway import ModelProviderIdentity
from bukmatika.ai.routes import AIAvailabilityState, ai_provider_status, grounded_research_answer
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.personalization import ContextSelectionDenied
from bukmatika.research import ReaderResearchContextRequest, ResearchEvidenceBundleRequest


class _ContextDeniedService:
    async def answer(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise ContextSelectionDenied("Selected library entry is unavailable")


class _DisabledProfileService:
    async def profile(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return SimpleNamespace(ai_enabled=False)


class _ProbeForbiddenGateway:
    def __init__(self) -> None:
        self._identity = ModelProviderIdentity(
            provider="ollama",
            model="qwen3:8b",
            routing="local",
        )
        self.readiness_calls = 0

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self):  # type: ignore[no-untyped-def]
        self.readiness_calls += 1
        raise AssertionError("AI-off status must not probe the local model runtime")


def _identity() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=uuid4(),
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def test_ai_off_status_does_not_probe_local_runtime() -> None:
    gateway = _ProbeForbiddenGateway()

    result = await ai_provider_status(
        identity=_identity(),
        gateway=gateway,  # type: ignore[arg-type]
        profile_service=_DisabledProfileService(),  # type: ignore[arg-type]
    )

    assert gateway.readiness_calls == 0
    assert result.ai_enabled is False
    assert result.ready is False
    assert result.configured is True
    assert result.state is AIAvailabilityState.AI_DISABLED
    assert result.provider == "ollama"
    assert result.model == "qwen3:8b"


async def test_grounded_answer_maps_context_ownership_denial_to_404() -> None:
    entry_id = uuid4()
    request = ResearchEvidenceBundleRequest(
        question="What does this passage establish?",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry_id,
            document_id=uuid4(),
            section_id=uuid4(),
            char_offset=0,
        ),
        library_entry_ids=[entry_id],
        related_limit=0,
    )

    with pytest.raises(HTTPException) as captured:
        await grounded_research_answer(
            request=request,
            identity=_identity(),
            service=_ContextDeniedService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == {"code": "RESEARCH_SELECTION_UNAVAILABLE"}
