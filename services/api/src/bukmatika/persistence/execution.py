from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.domain import CapabilityName, PlanStep
from bukmatika.persistence.personalization_models import ActionDecision, Plan, UserModel
from bukmatika.personalization.domain import ContextManifest


class ActionExecutionNotFound(LookupError):
    code = "ACTION_EXECUTION_NOT_FOUND"


class PlanIntegrityError(RuntimeError):
    code = "PLAN_INTEGRITY_ERROR"


@dataclass(frozen=True, slots=True)
class PersistedExecutionState:
    plan_id: UUID
    plan_status: str
    step: PlanStep
    decision: ActionDecision
    context: ContextManifest
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int


class ExecutionRepository:
    """Loads one principal-owned planned action and its current AI settings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(
        self,
        *,
        principal_id: UUID,
        plan_id: UUID,
        step_id: str,
    ) -> PersistedExecutionState:
        plan = await self._session.scalar(
            select(Plan).where(
                Plan.id == plan_id,
                Plan.principal_id == principal_id,
            )
        )
        if plan is None:
            raise ActionExecutionNotFound("Plan is unavailable")
        if plan.status not in {"proposed", "approved", "running"}:
            raise PlanIntegrityError(f"Plan status is not executable: {plan.status}")

        try:
            context = ContextManifest.model_validate(plan.context_manifest)
            steps = [PlanStep.model_validate(value) for value in plan.steps]
        except ValidationError as exc:
            raise PlanIntegrityError("Persisted plan context or steps are invalid") from exc

        matches = [step for step in steps if step.step_id == step_id]
        if len(matches) != 1:
            raise ActionExecutionNotFound("Planned step is unavailable")
        step = matches[0]

        decision = await self._session.scalar(
            select(ActionDecision).where(
                ActionDecision.plan_id == plan.id,
                ActionDecision.principal_id == principal_id,
                ActionDecision.step_id == step_id,
            )
        )
        if decision is None:
            raise PlanIntegrityError("Planned step has no deterministic action decision")
        if decision.capability != step.capability.value:
            raise PlanIntegrityError("Persisted action decision capability does not match the plan")
        try:
            CapabilityName(decision.capability)
        except ValueError as exc:
            raise PlanIntegrityError("Persisted action decision capability is unknown") from exc

        user_model = await self._session.scalar(
            select(UserModel).where(UserModel.principal_id == principal_id)
        )
        if user_model is None:
            raise PlanIntegrityError("Principal has no durable user model")

        return PersistedExecutionState(
            plan_id=plan.id,
            plan_status=plan.status,
            step=step,
            decision=decision,
            context=context,
            ai_enabled=user_model.ai_enabled,
            learning_enabled=user_model.learning_enabled,
            autonomy_level=user_model.autonomy_level,
        )
