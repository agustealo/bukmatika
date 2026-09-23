from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai import ActionPolicy, CapabilityName, CapabilityRegistry, PlanProposal, PlanStep
from bukmatika.ai.approval_domain import ActionApprovalRequest, UserApprovalDecision
from bukmatika.ai.approvals import ApprovalService
from bukmatika.ai.execution import ExecutionCoordinator
from bukmatika.main import app
from bukmatika.persistence.action_models import ActionExecutionReceipt
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import ActionApproval
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import ContextManifest, ContextTask
from bukmatika.personalization.portability import PersonalizationPortabilityService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_personalization_reset_cascades_approval_and_execution_receipt(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="approval-reset")
    session.add(principal)
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)

    step = PlanStep(
        step_id="remember-format",
        capability=CapabilityName.PREFERENCES_PROPOSE,
        arguments={
            "key": "format.preferred",
            "value": {"format": "EPUB"},
            "scope_type": "global",
            "scope_value": "",
            "influence": {
                "ranking": True,
                "presentation": True,
                "automation": False,
            },
        },
        rationale="Remember the explicitly approved format preference.",
    )
    context = ContextManifest(
        task=ContextTask.READER,
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
    policy = ActionPolicy(CapabilityRegistry()).evaluate(step, context)
    plan, decisions = await PlanRepository(session).create(
        principal_id=principal.id,
        user_request="Remember EPUB if I approve it.",
        proposal=PlanProposal(summary="Propose one reversible preference.", steps=[step]),
        context=context,
        decisions=[policy],
    )
    action_decision = decisions[0]
    scope = _scope(session)

    await ApprovalService(session_scope_factory=scope).decide(
        principal_id=principal.id,
        action_decision_id=action_decision.id,
        request=ActionApprovalRequest(decision=UserApprovalDecision.APPROVED),
    )
    await ExecutionCoordinator(session_scope_factory=scope).execute(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id=step.step_id,
    )

    assert await session.scalar(
        select(ActionApproval).where(ActionApproval.action_decision_id == action_decision.id)
    ) is not None
    assert await session.scalar(
        select(ActionExecutionReceipt).where(
            ActionExecutionReceipt.action_decision_id == action_decision.id
        )
    ) is not None

    await PersonalizationPortabilityService(session_scope_factory=scope).reset(
        principal_id=principal.id
    )

    assert await session.scalar(
        select(ActionApproval).where(ActionApproval.action_decision_id == action_decision.id)
    ) is None
    assert await session.scalar(
        select(ActionExecutionReceipt).where(
            ActionExecutionReceipt.action_decision_id == action_decision.id
        )
    ) is None


def test_approval_routes_are_mounted() -> None:
    paths = set(app.openapi()["paths"])
    assert "/v1/ai/approvals/pending" in paths
    assert "/v1/ai/actions/{action_decision_id}/approval" in paths
    assert "/v1/ai/plans/{plan_id}/steps/{step_id}/execute" in paths
