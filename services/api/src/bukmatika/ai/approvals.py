from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.approval_domain import (
    ActionApprovalRequest,
    ActionApprovalResponse,
    PendingActionApproval,
    PendingActionApprovalResponse,
    UserApprovalDecision,
    action_step_fingerprint,
)
from bukmatika.ai.domain import CapabilityName
from bukmatika.persistence import session_scope
from bukmatika.persistence.approvals import (
    ActionableApprovalTarget,
    ApprovalRepository,
    ApprovalTarget,
)
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.personalization_models import ActionApproval

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ApprovalService:
    """Records final user approval/rejection for exact persisted planned actions."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def decide(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        request: ActionApprovalRequest,
    ) -> ActionApprovalResponse:
        async with self._session_scope() as database_session:
            approval, target, created = await ApprovalRepository(database_session).decide(
                principal_id=principal_id,
                action_decision_id=action_decision_id,
                decision_value=request.decision,
            )
            if created:
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.ACTION_APPROVAL_DECIDED,
                    principal_id=principal_id,
                    entity_type="action_decision",
                    entity_id=action_decision_id,
                    context={
                        "decision": approval.decision,
                        "plan_id": str(approval.plan_id),
                        "step_id": target.step.step_id,
                        "capability": target.step.capability.value,
                        "step_fingerprint": approval.step_fingerprint,
                    },
                )
            return _response(approval, target)

    async def pending(
        self,
        *,
        principal_id: UUID,
        limit: int = 50,
    ) -> PendingActionApprovalResponse:
        async with self._session_scope() as database_session:
            targets = await ApprovalRepository(database_session).actionable_targets(
                principal_id=principal_id,
                limit=limit,
            )
            return PendingActionApprovalResponse(
                items=[_pending(item) for item in targets]
            )


def _response(approval: ActionApproval, target: ApprovalTarget) -> ActionApprovalResponse:
    return ActionApprovalResponse(
        approval_id=approval.id,
        principal_id=approval.principal_id,
        plan_id=approval.plan_id,
        action_decision_id=approval.action_decision_id,
        step_id=target.step.step_id,
        capability=CapabilityName(target.decision.capability),
        decision=UserApprovalDecision(approval.decision),
        step_fingerprint=approval.step_fingerprint,
        decided_at=approval.decided_at,
    )


def _pending(item: ActionableApprovalTarget) -> PendingActionApproval:
    target = item.target
    return PendingActionApproval(
        plan_id=target.plan.id,
        action_decision_id=target.decision.id,
        step_id=target.step.step_id,
        capability=target.step.capability,
        arguments=target.step.arguments,
        rationale=target.step.rationale,
        user_request=target.plan.user_request,
        policy_reason=target.decision.reason,
        policy_version=target.decision.policy_version,
        step_fingerprint=action_step_fingerprint(
            plan_id=target.plan.id,
            action_decision_id=target.decision.id,
            step=target.step,
        ),
        evaluated_at=target.decision.evaluated_at,
        approval_status=(
            UserApprovalDecision(item.approval.decision)
            if item.approval is not None
            else None
        ),
    )
