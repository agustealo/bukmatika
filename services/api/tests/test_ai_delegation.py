from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationAttemptCompletion,
    DelegationBudgetExceeded,
    DelegationConflict,
    DelegationExecutionDisabled,
    DelegationInvalid,
    DelegationNotFound,
    DelegationProposalRequest,
    DelegationStatus,
    DelegationStepUnavailable,
    DelegationStopRequested,
)
from bukmatika.ai.domain import CapabilityName, PlanProposal, PlanStep
from bukmatika.ai.policy import ActionPolicy
from bukmatika.persistence.action_models import ActionExecutionReceipt
from bukmatika.persistence.delegation_models import AIDelegation, AIDelegationAttempt
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import Plan, UserModel
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import ContextLibraryEntry, ContextManifest, ContextTask

RESEARCH_ENTRY_ID = UUID(int=101)
RESEARCH_WORK_ID = UUID(int=102)
RESEARCH_EDITION_ID = UUID(int=103)
RESEARCH_DOCUMENT_ID = UUID(int=104)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"delegation-{suffix}")
    session.add(principal)
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    return principal


def _context(*capabilities: CapabilityName) -> ContextManifest:
    selected_research_entries = (
        [
            ContextLibraryEntry(
                library_entry_id=RESEARCH_ENTRY_ID,
                work_id=RESEARCH_WORK_ID,
                edition_id=RESEARCH_EDITION_ID,
                title="Selected delegation contract book",
                document_ids=[RESEARCH_DOCUMENT_ID],
                inclusion_reason="Explicitly selected for delegated research.",
            )
        ]
        if CapabilityName.RESEARCH_SEARCH in capabilities
        else []
    )
    return ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=0,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=selected_research_entries,
        available_capabilities=[capability.value for capability in capabilities],
        exclusion_reasons=[],
    )


def _step(
    capability: CapabilityName,
    *,
    step_id: str = "research",
    arguments: dict[str, object] | None = None,
) -> PlanStep:
    return PlanStep.model_validate(
        {
            "step_id": step_id,
            "capability": capability.value,
            "arguments": arguments
            or {
                "query": "bounded delegation evidence",
                "library_entry_ids": [str(RESEARCH_ENTRY_ID)],
                "limit": 5,
            },
            "rationale": "Exercise only the durable delegation control plane.",
        }
    )


async def _plan(
    session: AsyncSession,
    *,
    principal_id: UUID,
    steps: list[PlanStep],
) -> Plan:
    registry = CapabilityRegistry()
    context = _context(*(step.capability for step in steps))
    decisions = [ActionPolicy(registry).evaluate(step, context) for step in steps]
    plan, _ = await PlanRepository(session).create(
        principal_id=principal_id,
        user_request="Prepare a bounded research delegation without executing it.",
        proposal=PlanProposal(summary="Bounded research control plan.", steps=steps),
        context=context,
        decisions=decisions,
    )
    return plan


def _proposal(
    *step_ids: str,
    retries: int = 0,
    total_attempts: int | None = None,
) -> DelegationProposalRequest:
    attempt_budget = total_attempts if total_attempts is not None else len(step_ids) * (retries + 1)
    return DelegationProposalRequest(
        step_ids=list(step_ids),
        max_runtime_seconds=300,
        max_retries_per_step=retries,
        max_total_attempts=attempt_budget,
    )


async def _approve(
    service: DelegationControlService,
    *,
    principal_id: UUID,
    delegation_id: UUID,
) -> None:
    await service.decide(
        principal_id=principal_id,
        delegation_id=delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.APPROVED),
    )


async def _enable_level_two(session: AsyncSession, principal_id: UUID) -> None:
    user_model = await session.scalar(
        select(UserModel).where(UserModel.principal_id == principal_id)
    )
    assert user_model is not None
    user_model.autonomy_level = 2
    await session.flush()


async def test_delegation_approval_is_exact_idempotent_and_principal_scoped(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    plan = await _plan(
        session,
        principal_id=owner.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))

    proposed = await service.propose(
        principal_id=owner.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    assert proposed.status is DelegationStatus.PROPOSED
    assert len(proposed.plan_fingerprint) == 64
    assert len(proposed.delegation_fingerprint) == 64

    with pytest.raises(DelegationNotFound):
        await service.get(principal_id=other.id, delegation_id=proposed.delegation_id)

    approved = await service.decide(
        principal_id=owner.id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.APPROVED),
    )
    replay = await service.decide(
        principal_id=owner.id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.APPROVED),
    )
    assert approved.status is DelegationStatus.APPROVED
    assert replay.delegation_fingerprint == approved.delegation_fingerprint
    assert replay.approval_decision is DelegationApprovalDecision.APPROVED

    with pytest.raises(DelegationConflict):
        await service.decide(
            principal_id=owner.id,
            delegation_id=proposed.delegation_id,
            request=DelegationApprovalRequest(decision=DelegationApprovalDecision.REJECTED),
        )


async def test_malformed_delegated_arguments_fail_before_proposal_persistence(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "invalid-preflight")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[
            _step(
                CapabilityName.RESEARCH_SEARCH,
                arguments={"query": "", "library_entry_ids": [], "limit": 5},
            )
        ],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))

    with pytest.raises(DelegationInvalid, match="arguments"):
        await service.propose(
            principal_id=principal.id,
            plan_id=plan.id,
            request=_proposal("research"),
        )

    assert await session.scalar(
        select(func.count(AIDelegation.id)).where(AIDelegation.plan_id == plan.id)
    ) == 0


async def test_only_explicitly_delegatable_read_only_capabilities_can_enter_control_plane(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "capability-fence")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.CATALOG_SEARCH, step_id="catalog")],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))

    with pytest.raises(DelegationStepUnavailable):
        await service.propose(
            principal_id=principal.id,
            plan_id=plan.id,
            request=_proposal("catalog"),
        )


async def test_public_level_one_cannot_activate_and_attempt_permit_never_executes_capability(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "permit-only")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await service.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(
        service,
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )

    with pytest.raises(DelegationExecutionDisabled):
        await service.activate(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )

    await _enable_level_two(session, principal.id)
    running = await service.activate(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert running.status is DelegationStatus.RUNNING

    permit = await service.authorize_next_step(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert permit.step_id == "research"
    assert permit.attempt_number == 1
    assert await session.scalar(
        select(func.count(ActionExecutionReceipt.id)).where(
            ActionExecutionReceipt.plan_id == plan.id
        )
    ) == 0
    attempt = await session.get(AIDelegationAttempt, permit.attempt_id)
    assert attempt is not None
    assert attempt.status == "authorized"


async def test_attempt_authorization_revalidates_contract_before_consuming_attempt(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "attempt-preflight")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await service.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(
        service,
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    await _enable_level_two(session, principal.id)
    await service.activate(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )

    mutated_step = dict(plan.steps[0])
    mutated_step["arguments"] = {"query": "", "library_entry_ids": [], "limit": 5}
    plan.steps = [mutated_step]
    await session.flush()

    with pytest.raises(DelegationInvalid, match="arguments"):
        await service.authorize_next_step(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )

    assert await session.scalar(
        select(func.count(AIDelegationAttempt.id)).where(
            AIDelegationAttempt.delegation_id == proposed.delegation_id
        )
    ) == 0


async def test_stop_request_survives_service_restart_and_blocks_new_attempts(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "stop")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    first_service = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await first_service.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(
        first_service,
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    await _enable_level_two(session, principal.id)
    await first_service.activate(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    requested = await first_service.request_stop(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert requested.status is DelegationStatus.STOP_REQUESTED

    restarted_service = DelegationControlService(session_scope_factory=_scope(session))
    with pytest.raises(DelegationStopRequested):
        await restarted_service.authorize_next_step(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )
    stopped = await restarted_service.acknowledge_stop(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert stopped.status is DelegationStatus.STOPPED
    assert stopped.stopped_at is not None


async def test_total_attempt_budget_exhaustion_is_durable_before_error_surfaces(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "attempt-budget")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await service.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research", retries=1, total_attempts=1),
    )
    await _approve(
        service,
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    await _enable_level_two(session, principal.id)
    await service.activate(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    permit = await service.authorize_next_step(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    after_failure = await service.complete_attempt(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
        attempt_id=permit.attempt_id,
        completion=DelegationAttemptCompletion(
            succeeded=False,
            error_code="PROBE_FAILURE",
        ),
    )
    assert after_failure.status is DelegationStatus.RUNNING

    with pytest.raises(DelegationBudgetExceeded):
        await service.authorize_next_step(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )
    stored = await session.get(AIDelegation, proposed.delegation_id)
    assert stored is not None
    assert stored.status == DelegationStatus.FAILED.value
    assert stored.failure_code == "attempt_budget_exhausted"


async def test_runtime_budget_exhaustion_is_durable_and_prevents_permit(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "runtime-budget")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    service = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await service.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(
        service,
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    await _enable_level_two(session, principal.id)
    await service.activate(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    stored = await session.get(AIDelegation, proposed.delegation_id)
    assert stored is not None
    stored.started_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    with pytest.raises(DelegationBudgetExceeded):
        await service.authorize_next_step(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )
    await session.refresh(stored)
    assert stored.status == DelegationStatus.FAILED.value
    assert stored.failure_code == "runtime_budget_exhausted"
