from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.domain import (
    ActionDecisionValue,
    CapabilityRisk,
    StepPolicyDecision,
    ValidatedPlannerStep,
)
from bukmatika.personalization.domain import ContextManifest

POLICY_VERSION = "ai-action-policy-v1"


class ActionPolicyEvaluator:
    """Deterministic policy authority for proposed AI actions."""

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or CapabilityRegistry()

    def evaluate(
        self,
        step: ValidatedPlannerStep,
        *,
        context: ContextManifest,
    ) -> StepPolicyDecision:
        if not context.ai_enabled or not context.model_context_ready:
            return self._decision(
                step,
                ActionDecisionValue.DENY,
                "AI is disabled or no model context is authorized.",
            )

        spec = self._registry.get(step.capability)
        if spec is None:
            return self._decision(
                step,
                ActionDecisionValue.DENY,
                "Capability is not registered.",
            )
        if spec.name not in context.available_capabilities:
            return self._decision(
                step,
                ActionDecisionValue.DENY,
                "Capability is not available in the current context.",
            )
        if spec.risk is CapabilityRisk.READ_ONLY:
            return self._decision(
                step,
                ActionDecisionValue.ALLOW,
                "Read-only capability is allowed within the validated context.",
            )
        if spec.risk is CapabilityRisk.NETWORK_READ:
            return self._decision(
                step,
                ActionDecisionValue.REQUIRE_APPROVAL,
                "Network activity requires explicit approval at current autonomy levels.",
            )
        return self._decision(
            step,
            ActionDecisionValue.REQUIRE_APPROVAL,
            "Consequential capability requires deterministic approval outside model output.",
        )

    @staticmethod
    def _decision(
        step: ValidatedPlannerStep,
        decision: ActionDecisionValue,
        reason: str,
    ) -> StepPolicyDecision:
        return StepPolicyDecision(
            step_id=step.step_id,
            capability=step.capability,
            decision=decision,
            reason=reason,
            policy_version=POLICY_VERSION,
        )
