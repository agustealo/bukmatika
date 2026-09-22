from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.domain import PlanProposal
from bukmatika.ai.policy import PolicyDecision
from bukmatika.persistence.personalization_models import ActionDecision, Plan
from bukmatika.personalization.domain import ContextManifest

PLANNER_VERSION = "structured-planner-v1"


class PlanRepository:
    """Persists validated plans and deterministic decisions in one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        principal_id: UUID,
        user_request: str,
        proposal: PlanProposal,
        context: ContextManifest,
        decisions: list[PolicyDecision],
    ) -> tuple[Plan, list[ActionDecision]]:
        if len(proposal.steps) != len(decisions):
            raise ValueError("Every plan step requires exactly one policy decision")

        plan = Plan(
            principal_id=principal_id,
            goal_id=context.goal.goal_id if context.goal is not None else None,
            status="proposed",
            user_request=user_request,
            planner_version=PLANNER_VERSION,
            steps=[step.model_dump(mode="json") for step in proposal.steps],
            context_manifest=context.model_dump(mode="json"),
        )
        self._session.add(plan)
        await self._session.flush()

        stored_decisions = [
            ActionDecision(
                principal_id=principal_id,
                plan_id=plan.id,
                step_id=step.step_id,
                capability=step.capability.value,
                decision=decision.decision.value,
                reason=decision.reason,
                policy_version=decision.policy_version,
            )
            for step, decision in zip(proposal.steps, decisions, strict=True)
        ]
        self._session.add_all(stored_decisions)
        await self._session.flush()
        return plan, stored_decisions
