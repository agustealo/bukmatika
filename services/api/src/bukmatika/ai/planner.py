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
            self._validate_context_bindings(spec.name, arguments, context=context)
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

    @staticmethod
    def _validate_context_bindings(
        capability: str,
        arguments: dict[str, object],
        *,
        context: ContextManifest,
    ) -> None:
        selected = {
            str(entry.library_entry_id): {str(document_id) for document_id in entry.document_ids}
            for entry in context.library_entries
        }
        if capability == "research.search":
            requested = set(arguments.get("library_entry_ids", []))
            if not requested.issubset(selected):
                raise PlannerValidationError(
                    "CONTEXT_ENTITY_UNAVAILABLE",
                    "Research plan referenced a library entry outside the authorized context",
                )
        elif capability == "reader.open":
            entry_id = str(arguments.get("library_entry_id", ""))
            document_id = str(arguments.get("document_id", ""))
            documents = selected.get(entry_id)
            if documents is None or document_id not in documents:
                raise PlannerValidationError(
                    "CONTEXT_ENTITY_UNAVAILABLE",
                    "Reader plan referenced an entry/document pair outside the authorized context",
                )
