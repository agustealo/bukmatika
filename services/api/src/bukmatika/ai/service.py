from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.domain import (
    PersistedPlanResponse,
    PlannedActionDecision,
    PlanProposal,
)
from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelGateway,
    ModelRequest,
    ModelTask,
    UnconfiguredModelGateway,
)
from bukmatika.ai.policy import ActionPolicy
from bukmatika.persistence import session_scope
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import ContextRequest

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class AIDisabled(RuntimeError):
    code = "AI_DISABLED"


class AIContextUnavailable(RuntimeError):
    code = "AI_CONTEXT_UNAVAILABLE"


class PlanningService:
    """Validates model proposals before deterministic policy and persistence."""

    def __init__(
        self,
        *,
        gateway: ModelGateway | None = None,
        registry: CapabilityRegistry | None = None,
        context_assembler: ContextAssembler | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._gateway = gateway or UnconfiguredModelGateway()
        self._registry = registry or CapabilityRegistry()
        self._context_assembler = context_assembler or ContextAssembler(
            session_scope_factory=session_scope_factory
        )
        self._session_scope = session_scope_factory
        self._policy = ActionPolicy(self._registry)

    async def propose(
        self,
        *,
        principal_id: UUID,
        user_request: str,
        context_request: ContextRequest,
    ) -> PersistedPlanResponse:
        normalized_request = " ".join(user_request.split())
        if not normalized_request or len(normalized_request) > 4_000:
            raise ValueError("User request must contain between 1 and 4,000 characters")

        context = await self._context_assembler.assemble(
            principal_id=principal_id,
            request=context_request,
        )
        if not context.ai_enabled:
            raise AIDisabled("AI is disabled for this principal")
        if not context.model_context_ready:
            raise AIContextUnavailable("Model context is not available")

        proposal = await self._gateway.generate_structured(
            ModelRequest(
                task=ModelTask.PLAN,
                payload={
                    "user_request": normalized_request,
                    "context": context.model_dump(mode="json"),
                },
                data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
                max_output_tokens=2_048,
                timeout_seconds=30,
            ),
            PlanProposal,
        )
        self._registry.validate_plan(proposal, context)
        decisions = [self._policy.evaluate(step, context) for step in proposal.steps]

        async with self._session_scope() as database_session:
            plan, stored_decisions = await PlanRepository(database_session).create(
                principal_id=principal_id,
                user_request=normalized_request,
                proposal=proposal,
                context=context,
                decisions=decisions,
            )

        return PersistedPlanResponse(
            plan_id=plan.id,
            status=plan.status,
            summary=proposal.summary,
            context=context,
            steps=proposal.steps,
            decisions=[
                PlannedActionDecision(
                    step_id=stored.step_id,
                    capability=stored.capability,
                    decision=stored.decision,
                    reason=stored.reason,
                    policy_version=stored.policy_version,
                )
                for stored in stored_decisions
            ],
        )
