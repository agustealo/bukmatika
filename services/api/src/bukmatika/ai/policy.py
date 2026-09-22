from dataclasses import dataclass
from enum import StrEnum

from bukmatika.ai.capabilities import CapabilityRegistry, CapabilityRisk
from bukmatika.ai.domain import PlanStep
from bukmatika.personalization.domain import ContextManifest

POLICY_VERSION = "ai-action-policy-v1"


class ActionDecisionValue(StrEnum):
    ALLOW = "allow"
    ALLOW_WITH_NOTIFICATION = "allow_with_notification"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: ActionDecisionValue
    reason: str
    policy_version: str = POLICY_VERSION


class ActionPolicy:
    """Deterministic authority over proposed AI actions."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def evaluate(self, step: PlanStep, context: ContextManifest) -> PolicyDecision:
        if not context.ai_enabled or not context.model_context_ready:
            return PolicyDecision(
                ActionDecisionValue.DENY,
                "AI context is disabled or unavailable.",
            )
        if context.autonomy_level not in (0, 1):
            return PolicyDecision(
                ActionDecisionValue.DENY,
                "Only autonomy Levels 0-1 are currently supported.",
            )
        if step.capability.value not in context.available_capabilities:
            return PolicyDecision(
                ActionDecisionValue.DENY,
                "Capability is not available in the validated context.",
            )

        spec = self._registry.get(step.capability)
        if spec.risk is CapabilityRisk.READ_ONLY:
            return PolicyDecision(
                ActionDecisionValue.ALLOW,
                "Read-only capability is allowed within the validated context.",
            )
        if spec.risk is CapabilityRisk.PROPOSAL:
            return PolicyDecision(
                ActionDecisionValue.REQUIRE_APPROVAL,
                "Personalization proposals require explicit user approval.",
            )
        if spec.risk is CapabilityRisk.CONSEQUENTIAL:
            return PolicyDecision(
                ActionDecisionValue.REQUIRE_APPROVAL,
                "Consequential actions require explicit approval and domain policy checks.",
            )
        return PolicyDecision(
            ActionDecisionValue.REQUIRE_APPROVAL,
            "Durable mutations require explicit approval at autonomy Levels 0-1.",
        )
