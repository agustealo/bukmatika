from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai import ActionPolicy, CapabilityName, CapabilityRegistry, PlanProposal, PlanStep
from bukmatika.ai.approval_domain import (
    ActionApprovalConflict,
    ActionApprovalInvalid,
    ActionApprovalNotFound,
    ActionApprovalRejected,
    ActionApprovalRequest,
    UserApprovalDecision,
)
from bukmatika.ai.approvals import ApprovalService
from bukmatika.ai.execution import (
    ActionExecutionDenied,
    AIExecutionDisabled,
    ExecutionCoordinator,
)
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import (
    ActionApproval,
    ActionDecision,
    Plan,
    PreferenceClaim,
)
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import ContextManifest, ContextTask


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"approval-{suffix}")
    session.add(principal)
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    return principal


def _context(*capabilities: CapabilityName) -> ContextManifest:
    return ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=0,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[],
        available_capabilities=[capability.value for capability in capabilities],
        exclusion_reasons=[],
    )


def _preference_step() -> PlanStep:
    return PlanStep(
        step_id="prefer-citations",
        capability=CapabilityName.PREFERENCES_PROPOSE,
        arguments={
            "key": "citation.style",
            "value": {"style": "Chicago"},
            "scope_type": "global",
            "scope_value": "",
            "influence": {
                "ranking": True,
                "presentation": True,
                "automation": False,
            },
        },
        rationale="Remember the citation style the user explicitly approves.",
    )


async def _persist_plan(
    session: AsyncSession,
    *,
    principal_id: UUID,
    step: PlanStep,
    context: ContextManifest,
) -> tuple[Plan, ActionDecision]:
    registry = CapabilityRegistry()
    decision = ActionPolicy(registry).evaluate(step, context)
    plan, decisions = await PlanRepository(session).create(
        principal_id=principal_id,
        user_request="Remember Chicago citations when I approve it.",
        proposal=PlanProposal(summary="Propose one preference.", steps=[step]),
        context=context,
        decisions=[decision],
    )
    return plan, decisions[0]


async def test_pending_approval_is_principal_scoped_and_exposes_exact_action(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "pending-owner")
    other = await _principal(session, "pending-other")
    step = _preference_step()
    plan, decision = await _persist_plan(
        session,
        principal_id=owner.id,
        step=step,
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )
    service = ApprovalService(session_scope_factory=_scope(session))

    owner_pending = await service.pending(principal_id=owner.id)
    other_pending = await service.pending(principal_id=other.id)

    assert other_pending.items == []
    assert len(owner_pending.items) == 1
    item = owner_pending.items[0]
    assert item.plan_id == plan.id
    assert item.action_decision_id == decision.id
    assert item.capability is CapabilityName.PREFERENCES_PROPOSE
    assert item.arguments == step.arguments
    assert item.rationale == step.rationale
    assert item.policy_reason == decision.reason
    assert len(item.step_fingerprint) == 64


async def test_approve_is_idempotent_and_decision_flip_is_rejected(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "idempotent")
    plan, decision = await _persist_plan(
        session,
        principal_id=principal.id,
        step=_preference_step(),
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )
    service = ApprovalService(session_scope_factory=_scope(session))
    request = ActionApprovalRequest(decision=UserApprovalDecision.APPROVED)

    first = await service.decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=request,
    )
    second = await service.decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=request,
    )

    assert first.approval_id == second.approval_id
    assert first.plan_id == plan.id
    approvals = list(
        (
            await session.scalars(
                select(ActionApproval).where(ActionApproval.action_decision_id == decision.id)
            )
        ).all()
    )
    assert len(approvals) == 1

    with pytest.raises(ActionApprovalConflict):
        await service.decide(
            principal_id=principal.id,
            action_decision_id=decision.id,
            request=ActionApprovalRequest(decision=UserApprovalDecision.REJECTED),
        )


async def test_another_principal_cannot_approve_action(session: AsyncSession) -> None:
    owner = await _principal(session, "owner")
    intruder = await _principal(session, "intruder")
    _, decision = await _persist_plan(
        session,
        principal_id=owner.id,
        step=_preference_step(),
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )

    with pytest.raises(ActionApprovalNotFound):
        await ApprovalService(session_scope_factory=_scope(session)).decide(
            principal_id=intruder.id,
            action_decision_id=decision.id,
            request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
        )


async def test_approved_preference_proposal_executes_through_canonical_personalization_service(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "execute")
    plan, decision = await _persist_plan(
        session,
        principal_id=principal.id,
        step=_preference_step(),
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )
    scope = _scope(session)
    await ApprovalService(session_scope_factory=scope).decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
    )

    result = await ExecutionCoordinator(session_scope_factory=scope).execute(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id="prefer-citations",
    )

    assert result.capability is CapabilityName.PREFERENCES_PROPOSE
    stored = await session.scalar(
        select(PreferenceClaim).where(
            PreferenceClaim.principal_id == principal.id,
            PreferenceClaim.key == "citation.style",
            PreferenceClaim.status == "active",
        )
    )
    assert stored is not None
    assert stored.source == "explicit"
    assert stored.value == {"style": "Chicago"}


async def test_rejected_preference_never_executes(session: AsyncSession) -> None:
    principal = await _principal(session, "rejected")
    plan, decision = await _persist_plan(
        session,
        principal_id=principal.id,
        step=_preference_step(),
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )
    scope = _scope(session)
    await ApprovalService(session_scope_factory=scope).decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.REJECTED),
    )

    with pytest.raises(ActionApprovalRejected):
        await ExecutionCoordinator(session_scope_factory=scope).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="prefer-citations",
        )

    assert await session.scalar(
        select(PreferenceClaim).where(
            PreferenceClaim.principal_id == principal.id,
            PreferenceClaim.key == "citation.style",
            PreferenceClaim.status == "active",
        )
    ) is None


async def test_approved_fingerprint_cannot_authorize_changed_step(session: AsyncSession) -> None:
    principal = await _principal(session, "stale")
    plan, decision = await _persist_plan(
        session,
        principal_id=principal.id,
        step=_preference_step(),
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )
    scope = _scope(session)
    await ApprovalService(session_scope_factory=scope).decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
    )
    changed = _preference_step().model_copy(
        update={
            "arguments": {
                **_preference_step().arguments,
                "value": {"style": "MLA"},
            }
        }
    )
    plan.steps = [changed.model_dump(mode="json")]
    await session.flush()

    with pytest.raises(ActionApprovalInvalid):
        await ExecutionCoordinator(session_scope_factory=scope).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="prefer-citations",
        )


async def test_ai_disabled_after_approval_blocks_execution(session: AsyncSession) -> None:
    principal = await _principal(session, "disabled")
    plan, decision = await _persist_plan(
        session,
        principal_id=principal.id,
        step=_preference_step(),
        context=_context(CapabilityName.PREFERENCES_PROPOSE),
    )
    scope = _scope(session)
    await ApprovalService(session_scope_factory=scope).decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
    )
    user_model = await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    user_model.ai_enabled = False
    await session.flush()

    with pytest.raises(AIExecutionDisabled):
        await ExecutionCoordinator(session_scope_factory=scope).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="prefer-citations",
        )


async def test_even_approved_acquisition_stays_non_executable(session: AsyncSession) -> None:
    principal = await _principal(session, "acquisition")
    step = PlanStep(
        step_id="acquire",
        capability=CapabilityName.ACQUISITION_REQUEST,
        arguments={"asset_id": str(UUID(int=9))},
        rationale="This consequential action must remain fenced.",
    )
    plan, decision = await _persist_plan(
        session,
        principal_id=principal.id,
        step=step,
        context=_context(CapabilityName.ACQUISITION_REQUEST),
    )
    scope = _scope(session)
    await ApprovalService(session_scope_factory=scope).decide(
        principal_id=principal.id,
        action_decision_id=decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
    )

    with pytest.raises(ActionExecutionDenied, match="Consequential"):
        await ExecutionCoordinator(session_scope_factory=scope).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="acquire",
        )
