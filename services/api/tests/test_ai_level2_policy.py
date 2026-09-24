from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.domain import CapabilityName, PlanStep
from bukmatika.ai.policy import ActionDecisionValue, ActionPolicy
from bukmatika.personalization.domain import ContextManifest, ContextTask


def _context(*, autonomy_level: int, capabilities: list[CapabilityName]) -> ContextManifest:
    return ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=autonomy_level,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[],
        available_capabilities=[capability.value for capability in capabilities],
        exclusion_reasons=[],
    )


def _step(capability: CapabilityName) -> PlanStep:
    return PlanStep(
        step_id="policy-step",
        capability=capability,
        arguments={},
        rationale="Exercise the deterministic action policy.",
    )


def test_level2_keeps_read_only_planning_eligible() -> None:
    policy = ActionPolicy(CapabilityRegistry())
    decision = policy.evaluate(
        _step(CapabilityName.RESEARCH_SEARCH),
        _context(
            autonomy_level=2,
            capabilities=[CapabilityName.RESEARCH_SEARCH],
        ),
    )

    assert decision.decision is ActionDecisionValue.ALLOW


def test_level2_does_not_auto_authorize_durable_writes() -> None:
    policy = ActionPolicy(CapabilityRegistry())
    decision = policy.evaluate(
        _step(CapabilityName.LIBRARY_SAVE),
        _context(
            autonomy_level=2,
            capabilities=[CapabilityName.LIBRARY_SAVE],
        ),
    )

    assert decision.decision is ActionDecisionValue.REQUIRE_APPROVAL


def test_unknown_future_autonomy_level_remains_denied() -> None:
    policy = ActionPolicy(CapabilityRegistry())
    decision = policy.evaluate(
        _step(CapabilityName.RESEARCH_SEARCH),
        _context(
            autonomy_level=3,
            capabilities=[CapabilityName.RESEARCH_SEARCH],
        ),
    )

    assert decision.decision is ActionDecisionValue.DENY
