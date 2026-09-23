from dataclasses import dataclass
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.approval_domain import (
    ActionApprovalConflict,
    ActionApprovalNotFound,
    ActionApprovalNotRequired,
    UserApprovalDecision,
    action_step_fingerprint,
)
from bukmatika.ai.domain import PlanStep
from bukmatika.persistence.personalization_models import ActionApproval, ActionDecision, Plan


@dataclass(frozen=True, slots=True)
class ApprovalTarget:
    plan: Plan
    decision: ActionDecision
    step: PlanStep


class ApprovalRepository:
    """Principal-owned durable approval authority for exact persisted plan steps."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def target(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
    ) -> ApprovalTarget:
        decision = await self._session.scalar(
            select(ActionDecision).where(
                ActionDecision.id == action_decision_id,
                ActionDecision.principal_id == principal_id,
            )
        )
        if decision is None:
            raise ActionApprovalNotFound("Action decision is unavailable")
        plan = await self._session.scalar(
            select(Plan).where(
                Plan.id == decision.plan_id,
                Plan.principal_id == principal_id,
            )
        )
        if plan is None:
            raise ActionApprovalNotFound("Action decision plan is unavailable")
        try:
            steps = [PlanStep.model_validate(item) for item in plan.steps]
        except ValidationError as exc:
            raise ActionApprovalNotFound("Persisted plan steps are invalid") from exc
        matches = [step for step in steps if step.step_id == decision.step_id]
        if len(matches) != 1:
            raise ActionApprovalNotFound("Action decision step is unavailable")
        step = matches[0]
        if step.capability.value != decision.capability:
            raise ActionApprovalNotFound("Action decision capability no longer matches its step")
        return ApprovalTarget(plan=plan, decision=decision, step=step)

    async def decide(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        decision_value: UserApprovalDecision,
    ) -> tuple[ActionApproval, ApprovalTarget]:
        target = await self.target(
            principal_id=principal_id,
            action_decision_id=action_decision_id,
        )
        if target.decision.decision != "require_approval":
            raise ActionApprovalNotRequired("Action decision does not require approval")
        fingerprint = action_step_fingerprint(
            plan_id=target.plan.id,
            action_decision_id=target.decision.id,
            step=target.step,
        )
        existing = await self._session.scalar(
            select(ActionApproval).where(
                ActionApproval.action_decision_id == target.decision.id,
                ActionApproval.principal_id == principal_id,
            )
        )
        if existing is not None:
            if existing.decision == decision_value.value and existing.step_fingerprint == fingerprint:
                return existing, target
            raise ActionApprovalConflict("Approval decision is final for this action")

        approval = ActionApproval(
            principal_id=principal_id,
            plan_id=target.plan.id,
            action_decision_id=target.decision.id,
            step_fingerprint=fingerprint,
            decision=decision_value.value,
        )
        self._session.add(approval)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ActionApprovalConflict("Approval decision already exists") from exc
        return approval, target

    async def approval_for_execution(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
    ) -> ActionApproval | None:
        return await self._session.scalar(
            select(ActionApproval).where(
                ActionApproval.action_decision_id == action_decision_id,
                ActionApproval.principal_id == principal_id,
            )
        )

    async def pending_targets(
        self,
        *,
        principal_id: UUID,
        limit: int = 50,
    ) -> list[ApprovalTarget]:
        rows = (
            await self._session.execute(
                select(Plan, ActionDecision)
                .outerjoin(
                    ActionApproval,
                    ActionApproval.action_decision_id == ActionDecision.id,
                )
                .where(
                    Plan.principal_id == principal_id,
                    ActionDecision.principal_id == principal_id,
                    ActionDecision.plan_id == Plan.id,
                    ActionDecision.decision == "require_approval",
                    ActionApproval.id.is_(None),
                )
                .order_by(ActionDecision.evaluated_at.desc(), ActionDecision.id.desc())
                .limit(limit)
            )
        ).all()
        targets: list[ApprovalTarget] = []
        for plan, decision in rows:
            try:
                steps = [PlanStep.model_validate(item) for item in plan.steps]
            except ValidationError:
                continue
            matches = [step for step in steps if step.step_id == decision.step_id]
            if len(matches) != 1 or matches[0].capability.value != decision.capability:
                continue
            targets.append(ApprovalTarget(plan=plan, decision=decision, step=matches[0]))
        return targets
