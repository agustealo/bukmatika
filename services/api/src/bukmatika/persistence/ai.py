from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.domain import StepPolicyDecision, ValidatedPlan
from bukmatika.persistence.personalization_models import ActionDecision, Plan
from bukmatika.personalization.domain import ContextManifest


class AIPlanRepository:
    """Persistence authority for validated plans and deterministic policy decisions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def persist_plan(
        self,
        *,
        principal_id: UUID,
        user_request: str,
        planner_version: str,
        plan: ValidatedPlan,
        context: ContextManifest,
        decisions: list[StepPolicyDecision],
    ) -> Plan:
        goal_id = context.goal.goal_id if context.goal is not None else None
        persisted = Plan(
            principal_id=principal_id,
            goal_id=goal_id,
            status="proposed",
            user_request=user_request,
            planner_version=planner_version,
            steps=[step.model_dump(mode="json") for step in plan.steps],
            context_manifest=context.model_dump(mode="json"),
        )
        self._session.add(persisted)
        await self._session.flush()

        self._session.add_all(
            [
                ActionDecision(
                    principal_id=principal_id,
                    plan_id=persisted.id,
                    step_id=decision.step_id,
                    capability=decision.capability,
                    decision=decision.decision.value,
                    reason=decision.reason,
                    policy_version=decision.policy_version,
                )
                for decision in decisions
            ]
        )
        await self._session.flush()
        return persisted
