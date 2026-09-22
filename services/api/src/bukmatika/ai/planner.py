from pydantic import ValidationError

from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.domain import (
    PlannerProposal,
    ValidatedPlan,
    ValidatedPlannerStep,
)
from bukmatika.personalization.domain import ContextManifest


class PlannerValidationError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PlannerValidator:
    """Validates structured planner output against registry and context authority."""

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or CapabilityRegistry()

    def validate(
        self,
        proposal: PlannerProposal,
        *,
        context: ContextManifest,
    ) -> ValidatedPlan:
        if not context.ai_enabled or not context.model_context_ready:
            raise PlannerValidationError(
                "AI_CONTEXT_DISABLED",
                "Planner output cannot be accepted while AI context is disabled",
            )

        available = set(context.available_capabilities)
        validated_steps: list[ValidatedPlannerStep] = []
        for step in proposal.steps:
            spec = self._registry.get(step.capability)
            if spec is None:
                raise PlannerValidationError(
                    "UNKNOWN_CAPABILITY",
                    f"Planner referenced unknown capability: {step.capability}",
                )
            if spec.name not in available:
                raise PlannerValidationError(
                    "CONTEXT_CAPABILITY_UNAVAILABLE",
                    f"Capability is unavailable in this context: {spec.name}",
                )
            try:
                arguments = spec.validate_arguments(step.arguments)
            except ValidationError as exc:
                raise PlannerValidationError(
                    "INVALID_CAPABILITY_ARGUMENTS",
                    f"Invalid arguments for {spec.name}: {exc.errors(include_url=False)}",
                ) from exc
            validated_steps.append(
                ValidatedPlannerStep(
                    step_id=step.step_id,
                    capability=spec.name,
                    arguments=arguments,
                    rationale=step.rationale,
                    risk=spec.risk,
                )
            )

        return ValidatedPlan(summary=proposal.summary, steps=validated_steps)
