import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select

from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationAttemptClaim,
    DelegationAttemptCompletion,
    DelegationConflict,
    DelegationProposalRequest,
    DelegationStatus,
)
from bukmatika.ai.delegation_runtime import DelegationRuntimeService
from bukmatika.ai.domain import CapabilityName, PlanProposal, PlanStep
from bukmatika.ai.policy import ActionPolicy
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegation_models import AIDelegation, AIDelegationAttempt
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import UserModel
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import ContextManifest, ContextTask


async def _committed_running_delegation() -> tuple[
    UUID,
    DelegationControlService,
    UUID,
]:
    external_subject = f"delegation-race-{uuid4()}"
    async with session_scope() as database_session:
        principal = Principal(kind="local", external_subject=external_subject)
        database_session.add(principal)
        await database_session.flush()
        await PersonalizationRepository(database_session).get_or_create_user_model(principal.id)
        context = ContextManifest(
            task=ContextTask.RESEARCH,
            ai_enabled=True,
            learning_enabled=True,
            autonomy_level=0,
            model_context_ready=True,
            preferences=[],
            goal=None,
            library_entries=[],
            available_capabilities=[CapabilityName.RESEARCH_SEARCH.value],
            exclusion_reasons=[],
        )
        step = PlanStep(
            step_id="research",
            capability=CapabilityName.RESEARCH_SEARCH,
            arguments={
                "query": "claim concurrency evidence",
                "library_entry_ids": [],
                "limit": 5,
            },
            rationale="Prove one runtime can own a delegated permit at a time.",
        )
        decision = ActionPolicy(CapabilityRegistry()).evaluate(step, context)
        plan, _ = await PlanRepository(database_session).create(
            principal_id=principal.id,
            user_request="Run one bounded delegated research step.",
            proposal=PlanProposal(summary="Concurrency claim proof.", steps=[step]),
            context=context,
            decisions=[decision],
        )
        principal_id = principal.id
        plan_id = plan.id

    control = DelegationControlService()
    proposed = await control.propose(
        principal_id=principal_id,
        plan_id=plan_id,
        request=DelegationProposalRequest(
            step_ids=["research"],
            max_runtime_seconds=300,
            max_retries_per_step=0,
            max_total_attempts=1,
        ),
    )
    await control.decide(
        principal_id=principal_id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.APPROVED),
    )
    async with session_scope() as database_session:
        user_model = await database_session.scalar(
            select(UserModel).where(UserModel.principal_id == principal_id)
        )
        assert user_model is not None
        user_model.autonomy_level = 2
    await control.activate(
        principal_id=principal_id,
        delegation_id=proposed.delegation_id,
    )
    return principal_id, control, proposed.delegation_id


async def _delete_principal(principal_id: UUID) -> None:
    async with session_scope() as database_session:
        principal = await database_session.get(Principal, principal_id)
        if principal is not None:
            await database_session.delete(principal)


async def test_two_runtime_sessions_cannot_claim_the_same_permit() -> None:
    principal_id, control, delegation_id = await _committed_running_delegation()
    try:
        permit = await control.authorize_next_step(
            principal_id=principal_id,
            delegation_id=delegation_id,
        )
        runtime_a = DelegationRuntimeService(
            control_service=control,
            claim_lease_seconds=2,
            execution_timeout_seconds=1,
        )
        runtime_b = DelegationRuntimeService(
            control_service=control,
            claim_lease_seconds=2,
            execution_timeout_seconds=1,
        )

        results = await asyncio.gather(
            runtime_a.claim_permit(principal_id=principal_id, permit=permit),
            runtime_b.claim_permit(principal_id=principal_id, permit=permit),
            return_exceptions=True,
        )

        claims = [result for result in results if isinstance(result, DelegationAttemptClaim)]
        conflicts = [result for result in results if isinstance(result, DelegationConflict)]
        assert len(claims) == 1
        assert len(conflicts) == 1

        completed = await runtime_a.settle_claim(
            principal_id=principal_id,
            claim=claims[0],
            completion=DelegationAttemptCompletion(succeeded=True),
        )
        assert completed.status is DelegationStatus.COMPLETED

        async with session_scope() as database_session:
            delegation = await database_session.get(AIDelegation, delegation_id)
            assert delegation is not None
            assert delegation.attempts_used == 1
            attempt = await database_session.scalar(
                select(AIDelegationAttempt).where(
                    AIDelegationAttempt.delegation_id == delegation_id
                )
            )
            assert attempt is not None
            assert attempt.status == "completed"
            assert attempt.claim_token is None
    finally:
        await _delete_principal(principal_id)
