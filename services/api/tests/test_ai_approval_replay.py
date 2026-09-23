from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai import ActionPolicy, CapabilityName, CapabilityRegistry, PlanProposal, PlanStep
from bukmatika.ai.approval_domain import (
    ActionApprovalRequest,
    UserApprovalDecision,
)
from bukmatika.ai.approvals import ApprovalService
from bukmatika.ai.execution import ExecutionCoordinator
from bukmatika.persistence.action_models import ActionExecutionReceipt
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import PreferenceClaim
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import (
    ContextManifest,
    ContextTask,
    ExplicitPreferenceRequest,
    PreferenceKey,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_replay_cannot_overwrite_newer_user_correction(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="approval-replay")
    session.add(principal)
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)

    step = PlanStep(
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
        rationale="Remember the approved citation style.",
    )
    context = ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=0,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[],
        available_capabilities=[CapabilityName.PREFERENCES_PROPOSE.value],
        exclusion_reasons=[],
    )
    policy_decision = ActionPolicy(CapabilityRegistry()).evaluate(step, context)
    plan, decisions = await PlanRepository(session).create(
        principal_id=principal.id,
        user_request="Use Chicago citations if I approve it.",
        proposal=PlanProposal(summary="Propose citation style.", steps=[step]),
        context=context,
        decisions=[policy_decision],
    )
    action_decision = decisions[0]
    scope = _scope(session)
    approval_service = ApprovalService(session_scope_factory=scope)
    coordinator = ExecutionCoordinator(session_scope_factory=scope)

    await approval_service.decide(
        principal_id=principal.id,
        action_decision_id=action_decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
    )
    approved_pending = await approval_service.pending(principal_id=principal.id)
    assert len(approved_pending.items) == 1
    assert approved_pending.items[0].approval_status is UserApprovalDecision.APPROVED

    first = await coordinator.execute(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id=step.step_id,
    )
    assert first.output["value"] == {"style": "Chicago"}
    assert (await approval_service.pending(principal_id=principal.id)).items == []

    await PersonalizationService(session_scope_factory=scope).set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "MLA"},
        ),
    )

    replay = await coordinator.execute(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id=step.step_id,
    )
    assert replay.output == first.output

    active = await session.scalar(
        select(PreferenceClaim).where(
            PreferenceClaim.principal_id == principal.id,
            PreferenceClaim.key == PreferenceKey.CITATION_STYLE.value,
            PreferenceClaim.status == "active",
        )
    )
    assert active is not None
    assert active.value == {"style": "MLA"}

    receipts = list(
        (
            await session.scalars(
                select(ActionExecutionReceipt).where(
                    ActionExecutionReceipt.action_decision_id == action_decision.id
                )
            )
        ).all()
    )
    assert len(receipts) == 1
