from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
)
from bukmatika.ai.grounded_smoke import run_grounded_runtime_proof
from bukmatika.research import ResearchEvidenceReferenceInvalid


class _GroundedProofGateway:
    def __init__(self, *, evidence_id: str = "E1") -> None:
        self._identity = ModelProviderIdentity(
            provider="ollama",
            model="qwen3:8b",
            routing="local",
        )
        self._evidence_id = evidence_id
        self.requests: list[ModelRequest] = []

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

    async def generate_structured(self, request, response_type):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return response_type.model_validate(
            {
                "claims": [
                    {
                        "text": "The navigators used Polaris and noon solar observations.",
                        "evidence_ids": [self._evidence_id],
                    }
                ]
            }
        )


async def test_grounded_runtime_proof_exercises_canonical_synthesis_without_raw_output(
    session: AsyncSession,
) -> None:
    gateway = _GroundedProofGateway()

    execution = await run_grounded_runtime_proof(
        cast(ModelGateway, gateway),
        session=session,
    )

    result = execution.result
    assert result.status == "passed"
    assert result.provider == "ollama"
    assert result.model == "qwen3:8b"
    assert result.routing == "local"
    assert result.evidence_count >= 1
    assert result.claim_count == 1
    assert result.cited_evidence_ids == ["E1"]
    assert result.audit_recorded is True
    assert result.ledger_projected is True
    assert result.rollback_verified is False

    assert len(gateway.requests) == 1
    request = gateway.requests[0]
    assert request.task is ModelTask.RESEARCH_ANSWER
    assert set(request.payload) == {"question", "evidence"}

    output = result.model_dump_json()
    assert "Polaris" not in output
    assert "solar" not in output
    assert "Which observations" not in output


async def test_grounded_runtime_proof_rejects_fabricated_model_citation(
    session: AsyncSession,
) -> None:
    gateway = _GroundedProofGateway(evidence_id="E999")

    with pytest.raises(ResearchEvidenceReferenceInvalid):
        await run_grounded_runtime_proof(
            cast(ModelGateway, gateway),
            session=session,
        )
