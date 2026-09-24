import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_delegation import (
    RESEARCH_ENTRY_ID,
    _plan,
    _principal,
    _proposal,
    _scope,
    _step,
)

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_control import DelegationOperatorControlService
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationConflict,
    DelegationStatus,
)
from bukmatika.ai.domain import CapabilityName
from bukmatika.persistence.personalization_models import ActionDecision


async def test_control_status_projects_exact_persisted_delegation_review(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "review-projection")
    step = _step(CapabilityName.RESEARCH_SEARCH)
    plan = await _plan(session, principal_id=principal.id, steps=[step])
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research", retries=2),
    )

    snapshot = await operator.status(principal_id=principal.id)

    assert len(snapshot.active_delegations) == 1
    item = snapshot.active_delegations[0]
    assert item.delegation_id == proposed.delegation_id
    assert item.review_valid is True
    assert item.user_request == "Prepare a bounded research delegation without executing it."
    assert item.max_retries_per_step == 2
    assert item.max_total_attempts == 3
    assert len(item.library_entries) == 1
    assert item.library_entries[0].library_entry_id == RESEARCH_ENTRY_ID
    assert item.library_entries[0].title == "Selected delegation contract book"
    assert len(item.steps) == 1
    assert item.steps[0].step_id == step.step_id
    assert item.steps[0].capability == CapabilityName.RESEARCH_SEARCH.value
    assert item.steps[0].arguments == step.arguments
    assert item.steps[0].rationale == step.rationale


async def test_stale_plan_review_fails_closed_before_approval_but_can_be_rejected(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "stale-review")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )

    plan.user_request = "Tampered after the delegation proposal was fingerprinted."
    await session.flush()

    snapshot = await operator.status(principal_id=principal.id)
    assert len(snapshot.active_delegations) == 1
    item = snapshot.active_delegations[0]
    assert item.review_valid is False
    assert item.user_request is None
    assert item.library_entries == []
    assert item.steps == []

    with pytest.raises(DelegationConflict):
        await control.decide(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
            request=DelegationApprovalRequest(
                decision=DelegationApprovalDecision.APPROVED,
            ),
        )

    rejected = await control.decide(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(
            decision=DelegationApprovalDecision.REJECTED,
        ),
    )
    assert rejected.status is DelegationStatus.REJECTED


async def test_stale_policy_is_reported_as_delegation_conflict_at_approval(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "stale-policy-review")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    decision = await session.scalar(select(ActionDecision).where(ActionDecision.plan_id == plan.id))
    assert decision is not None
    decision.policy_version = "retired-policy-version"
    await session.flush()

    with pytest.raises(DelegationConflict):
        await control.decide(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
            request=DelegationApprovalRequest(
                decision=DelegationApprovalDecision.APPROVED,
            ),
        )
