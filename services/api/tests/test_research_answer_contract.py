import pytest

from bukmatika.ai.capabilities import CapabilityRegistry, CapabilityRisk, PlanCapabilityUnavailable
from bukmatika.ai.domain import CapabilityName, PlanProposal, PlanStep
from bukmatika.ai.execution import CapabilityExecutorRegistry, CapabilityExecutorUnavailable
from bukmatika.personalization.domain import ContextManifest, ContextTask


def test_research_answer_is_registered_but_not_advertised_or_executable() -> None:
    registry = CapabilityRegistry()
    spec = registry.get(CapabilityName.RESEARCH_ANSWER)
    assert spec.risk is CapabilityRisk.READ_ONLY
    assert spec.reversible is True

    context = ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=1,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[],
        available_capabilities=["research.search", "reader.open"],
        exclusion_reasons=[],
    )
    proposal = PlanProposal(
        summary="Attempt grounded synthesis before a provider exists.",
        steps=[
            PlanStep(
                step_id="answer-1",
                capability=CapabilityName.RESEARCH_ANSWER,
                arguments={"evidence_ids": ["E1"]},
                rationale="Synthesize only from canonical evidence.",
            )
        ],
    )

    with pytest.raises(PlanCapabilityUnavailable, match="not available"):
        registry.validate_plan(proposal, context)

    with pytest.raises(CapabilityExecutorUnavailable, match=r"research\.answer"):
        CapabilityExecutorRegistry().get(CapabilityName.RESEARCH_ANSWER)
