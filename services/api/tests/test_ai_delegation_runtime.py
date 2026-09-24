import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import BaseModel, JsonValue
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationAttemptCompletion,
    DelegationConflict,
    DelegationProposalRequest,
    DelegationStatus,
    DelegationStepUnavailable,
)
from bukmatika.ai.delegation_runtime import (
    DelegatedExecutionAdapter,
    DelegationRuntimeService,
)
from bukmatika.ai.domain import CapabilityName, PlanProposal, PlanStep
from bukmatika.ai.execution import CapabilityExecutorRegistry
from bukmatika.ai.policy import ActionPolicy
from bukmatika.persistence.action_models import ActionExecutionReceipt
from bukmatika.persistence.delegation_models import AIDelegation, AIDelegationAttempt
from bukmatika.persistence.models import InteractionEvent, Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import Plan, UserModel
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import ContextManifest, ContextTask


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _SearchOutput(BaseModel):
    marker: str


class _SearchExecutor:
    capability = CapabilityName.RESEARCH_SEARCH

    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.calls = 0
        self.delay_seconds = delay_seconds

    async def execute(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel:
        del principal_id, action_decision_id, arguments
        assert context.autonomy_level == 2
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return _SearchOutput(marker="executed")


class _CatalogExecutor:
    capability = CapabilityName.CATALOG_SEARCH

    async def execute(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel:
        del principal_id, action_decision_id, arguments, context
        return _SearchOutput(marker="must-not-execute")


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"delegation-runtime-{suffix}")
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


def _step(capability: CapabilityName, *, step_id: str = "research") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        capability=capability,
        arguments={
            "query": "delegated runtime evidence",
            "library_entry_ids": [],
            "limit": 5,
        },
        rationale="Exercise one bounded delegated runtime attempt.",
    )


async def _plan(
    session: AsyncSession,
    *,
    principal_id: UUID,
    step: PlanStep,
) -> Plan:
    registry = CapabilityRegistry()
    context = _context(step.capability)
    decision = ActionPolicy(registry).evaluate(step, context)
    plan, _ = await PlanRepository(session).create(
        principal_id=principal_id,
        user_request="Run one bounded delegated research step.",
        proposal=PlanProposal(summary="Bounded delegated runtime plan.", steps=[step]),
        context=context,
        decisions=[decision],
    )
    return plan


async def _running_delegation(
    session: AsyncSession,
    *,
    suffix: str,
    capability: CapabilityName = CapabilityName.RESEARCH_SEARCH,
    retries: int = 0,
) -> tuple[Principal, Plan, DelegationControlService, UUID]:
    principal = await _principal(session, suffix)
    plan = await _plan(
        session,
        principal_id=principal.id,
        step=_step(capability, step_id="research"),
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=DelegationProposalRequest(
            step_ids=["research"],
            max_runtime_seconds=300,
            max_retries_per_step=retries,
            max_total_attempts=retries + 1,
        ),
    )
    await control.decide(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.APPROVED),
    )
    user_model = await session.scalar(
        select(UserModel).where(UserModel.principal_id == principal.id)
    )
    assert user_model is not None
    user_model.autonomy_level = 2
    await session.flush()
    await control.activate(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    return principal, plan, control, proposed.delegation_id


def _runtime(
    session: AsyncSession,
    *,
    control: DelegationControlService,
    executor: _SearchExecutor,
    timeout_seconds: float = 0.2,
    lease_seconds: float = 1.0,
) -> DelegationRuntimeService:
    adapter = DelegatedExecutionAdapter(
        executor_registry=CapabilityExecutorRegistry((executor,)),
        session_scope_factory=_scope(session),
    )
    return DelegationRuntimeService(
        control_service=control,
        execution_adapter=adapter,
        session_scope_factory=_scope(session),
        claim_lease_seconds=lease_seconds,
        execution_timeout_seconds=timeout_seconds,
    )


async def test_runtime_consumes_one_permit_through_canonical_executor_and_settles(
    session: AsyncSession,
) -> None:
    principal, plan, control, delegation_id = await _running_delegation(
        session,
        suffix="success",
    )
    executor = _SearchExecutor()
    runtime = _runtime(session, control=control, executor=executor)

    result = await runtime.run_once(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )

    assert executor.calls == 1
    assert result.execution.capability is CapabilityName.RESEARCH_SEARCH
    assert result.execution.output == {"marker": "executed"}
    assert result.delegation.status is DelegationStatus.COMPLETED
    attempt = await session.get(AIDelegationAttempt, result.claim.permit.attempt_id)
    assert attempt is not None
    assert attempt.status == "completed"
    assert attempt.claim_token is None
    assert attempt.claimed_at is None
    assert attempt.claim_expires_at is None
    assert await session.scalar(
        select(func.count(ActionExecutionReceipt.id)).where(ActionExecutionReceipt.plan_id == plan.id)
    ) == 0
    assert await session.scalar(
        select(func.count(InteractionEvent.id)).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "ai.delegation_attempt_claimed",
        )
    ) == 1


async def test_runtime_reclaims_expired_lease_without_spending_another_attempt(
    session: AsyncSession,
) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="reclaim",
    )
    executor = _SearchExecutor()
    runtime = _runtime(session, control=control, executor=executor)
    permit = await control.authorize_next_step(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    first = await runtime.claim_permit(principal_id=principal.id, permit=permit)
    attempt = await session.get(AIDelegationAttempt, permit.attempt_id)
    assert attempt is not None
    attempt.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    second = await runtime.claim_permit(principal_id=principal.id, permit=permit)

    assert second.claim_token != first.claim_token
    stored = await session.get(AIDelegation, delegation_id)
    assert stored is not None
    assert stored.attempts_used == 1
    with pytest.raises(DelegationConflict):
        await runtime.settle_claim(
            principal_id=principal.id,
            claim=first,
            completion=DelegationAttemptCompletion(succeeded=True),
        )
    completed = await runtime.settle_claim(
        principal_id=principal.id,
        claim=second,
        completion=DelegationAttemptCompletion(succeeded=True),
    )
    assert completed.status is DelegationStatus.COMPLETED


async def test_claimed_attempt_cannot_bypass_runtime_token_through_control_service(
    session: AsyncSession,
) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="claim-bypass",
    )
    runtime = _runtime(session, control=control, executor=_SearchExecutor())
    permit = await control.authorize_next_step(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    claim = await runtime.claim_permit(principal_id=principal.id, permit=permit)

    with pytest.raises(DelegationConflict):
        await control.complete_attempt(
            principal_id=principal.id,
            delegation_id=delegation_id,
            attempt_id=permit.attempt_id,
            completion=DelegationAttemptCompletion(succeeded=True),
        )
    settled = await runtime.settle_claim(
        principal_id=principal.id,
        claim=claim,
        completion=DelegationAttemptCompletion(succeeded=True),
    )
    assert settled.status is DelegationStatus.COMPLETED


async def test_stop_after_expired_claim_cancels_attempt_and_survives_runtime_restart(
    session: AsyncSession,
) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="stop-restart",
    )
    runtime = _runtime(session, control=control, executor=_SearchExecutor())
    permit = await control.authorize_next_step(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    await runtime.claim_permit(principal_id=principal.id, permit=permit)
    attempt = await session.get(AIDelegationAttempt, permit.attempt_id)
    assert attempt is not None
    attempt.claim_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    requested = await control.request_stop(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    assert requested.status is DelegationStatus.STOP_REQUESTED

    restarted = _runtime(session, control=control, executor=_SearchExecutor())
    stopped = await restarted.acknowledge_stop(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )

    assert stopped.status is DelegationStatus.STOPPED
    await session.refresh(attempt)
    assert attempt.status == "cancelled"
    assert attempt.error_code == "DELEGATION_STOPPED"
    assert attempt.claim_token is None
    assert await session.scalar(
        select(func.count(InteractionEvent.id)).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "ai.delegation_attempt_cancelled",
        )
    ) == 1


async def test_execution_timeout_is_durably_failed_before_timeout_surfaces(
    session: AsyncSession,
) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="timeout",
    )
    executor = _SearchExecutor(delay_seconds=0.1)
    runtime = _runtime(
        session,
        control=control,
        executor=executor,
        timeout_seconds=0.01,
        lease_seconds=0.5,
    )

    with pytest.raises(TimeoutError):
        await runtime.run_once(
            principal_id=principal.id,
            delegation_id=delegation_id,
        )

    stored = await session.get(AIDelegation, delegation_id)
    assert stored is not None
    assert stored.status == DelegationStatus.FAILED.value
    assert stored.failure_code == "DELEGATED_EXECUTION_TIMEOUT"
    attempt = await session.scalar(
        select(AIDelegationAttempt).where(AIDelegationAttempt.delegation_id == delegation_id)
    )
    assert attempt is not None
    assert attempt.status == "failed"
    assert attempt.error_code == "DELEGATED_EXECUTION_TIMEOUT"
    assert attempt.claim_token is None


async def test_delegated_adapter_refuses_read_only_capability_without_delegation_flag(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "adapter-fence")
    plan = await _plan(
        session,
        principal_id=principal.id,
        step=_step(CapabilityName.CATALOG_SEARCH, step_id="catalog"),
    )
    user_model = await session.scalar(
        select(UserModel).where(UserModel.principal_id == principal.id)
    )
    assert user_model is not None
    user_model.autonomy_level = 2
    await session.flush()
    adapter = DelegatedExecutionAdapter(
        executor_registry=CapabilityExecutorRegistry((_CatalogExecutor(),)),
        session_scope_factory=_scope(session),
    )

    from bukmatika.ai.delegation_domain import DelegationAttemptPermit

    with pytest.raises(DelegationStepUnavailable):
        await adapter.execute(
            principal_id=principal.id,
            permit=DelegationAttemptPermit(
                attempt_id=principal.id,
                delegation_id=principal.id,
                plan_id=plan.id,
                step_id="catalog",
                attempt_number=1,
                delegation_fingerprint="0" * 64,
                authorized_at=datetime.now(UTC),
            ),
        )
