from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.delegation_domain import DelegationNotFound
from bukmatika.persistence.delegation_models import (
    AIDelegation,
    AIDelegationApproval,
    AIDelegationAttempt,
)
from bukmatika.persistence.personalization_models import ActionDecision, Plan


@dataclass(frozen=True, slots=True)
class DelegationPlanBundle:
    plan: Plan
    decisions: tuple[ActionDecision, ...]


class DelegationRepository:
    """Principal-scoped, row-locked persistence authority for bounded delegation."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def plan_bundle(
        self,
        *,
        principal_id: UUID,
        plan_id: UUID,
    ) -> DelegationPlanBundle:
        plan = await self._session.scalar(
            select(Plan).where(
                Plan.id == plan_id,
                Plan.principal_id == principal_id,
            )
        )
        if plan is None:
            raise DelegationNotFound("Plan is unavailable for delegation")
        decisions = tuple(
            (
                await self._session.scalars(
                    select(ActionDecision)
                    .where(
                        ActionDecision.plan_id == plan_id,
                        ActionDecision.principal_id == principal_id,
                    )
                    .order_by(ActionDecision.evaluated_at, ActionDecision.step_id)
                )
            ).all()
        )
        return DelegationPlanBundle(plan=plan, decisions=decisions)

    async def create(
        self,
        *,
        principal_id: UUID,
        plan_id: UUID,
        selected_step_ids: list[str],
        plan_fingerprint: str,
        delegation_fingerprint: str,
        max_runtime_seconds: int,
        max_retries_per_step: int,
        max_total_attempts: int,
    ) -> AIDelegation:
        delegation = AIDelegation(
            principal_id=principal_id,
            plan_id=plan_id,
            status="proposed",
            selected_step_ids=selected_step_ids,
            plan_fingerprint=plan_fingerprint,
            delegation_fingerprint=delegation_fingerprint,
            max_runtime_seconds=max_runtime_seconds,
            max_retries_per_step=max_retries_per_step,
            max_total_attempts=max_total_attempts,
            attempts_used=0,
            current_step_index=0,
        )
        self._session.add(delegation)
        await self._session.flush()
        return delegation

    async def get(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
        lock: bool = False,
    ) -> AIDelegation:
        statement = select(AIDelegation).where(
            AIDelegation.id == delegation_id,
            AIDelegation.principal_id == principal_id,
        )
        if lock:
            statement = statement.with_for_update()
        delegation = await self._session.scalar(statement)
        if delegation is None:
            raise DelegationNotFound("Delegation is unavailable")
        return delegation

    async def approval(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> AIDelegationApproval | None:
        return await self._session.scalar(
            select(AIDelegationApproval).where(
                AIDelegationApproval.delegation_id == delegation_id,
                AIDelegationApproval.principal_id == principal_id,
            )
        )

    async def create_approval(
        self,
        *,
        delegation: AIDelegation,
        decision: str,
    ) -> AIDelegationApproval:
        approval = AIDelegationApproval(
            principal_id=delegation.principal_id,
            delegation_id=delegation.id,
            delegation_fingerprint=delegation.delegation_fingerprint,
            decision=decision,
        )
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def active_attempt(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> AIDelegationAttempt | None:
        return await self._session.scalar(
            select(AIDelegationAttempt).where(
                AIDelegationAttempt.principal_id == principal_id,
                AIDelegationAttempt.delegation_id == delegation_id,
                AIDelegationAttempt.status == "authorized",
            )
        )

    async def step_attempt_count(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
        step_id: str,
    ) -> int:
        return int(
            await self._session.scalar(
                select(func.count(AIDelegationAttempt.id)).where(
                    AIDelegationAttempt.principal_id == principal_id,
                    AIDelegationAttempt.delegation_id == delegation_id,
                    AIDelegationAttempt.step_id == step_id,
                )
            )
            or 0
        )

    async def create_attempt(
        self,
        *,
        delegation: AIDelegation,
        step_id: str,
        attempt_number: int,
    ) -> AIDelegationAttempt:
        attempt = AIDelegationAttempt(
            principal_id=delegation.principal_id,
            delegation_id=delegation.id,
            step_id=step_id,
            attempt_number=attempt_number,
            status="authorized",
        )
        delegation.attempts_used += 1
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def attempt_for_update(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
        attempt_id: UUID,
    ) -> AIDelegationAttempt:
        attempt = await self._session.scalar(
            select(AIDelegationAttempt)
            .where(
                AIDelegationAttempt.id == attempt_id,
                AIDelegationAttempt.principal_id == principal_id,
                AIDelegationAttempt.delegation_id == delegation_id,
            )
            .with_for_update()
        )
        if attempt is None:
            raise DelegationNotFound("Delegated attempt is unavailable")
        return attempt
