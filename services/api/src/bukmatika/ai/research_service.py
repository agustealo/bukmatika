from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry
from bukmatika.ai.domain import CapabilityName, PlanProposal, PlanStep
from bukmatika.ai.execution import (
    CapabilityExecutorRegistry,
    ExecutionCoordinator,
    ResearchAnswerExecutor,
    ResearchSearchExecutor,
)
from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderNotReady,
    ModelProviderReadiness,
    ModelProviderUnconfigured,
)
from bukmatika.ai.policy import ActionPolicy
from bukmatika.ai.research_domain import (
    GroundedResearchCapabilityOutput,
    GroundedResearchExecutionResponse,
)
from bukmatika.ai.service import AIDisabled
from bukmatika.persistence import session_scope
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import ContextRequest, ContextTask
from bukmatika.research import ResearchEvidenceBundleRequest, ResearchService

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class GroundedResearchSynthesisService:
    """Persists, policies, and executes one explicitly requested grounded answer."""

    def __init__(
        self,
        *,
        gateway: ModelGateway,
        session_scope_factory: SessionScopeFactory = session_scope,
        research_service: ResearchService | None = None,
    ) -> None:
        self._gateway = gateway
        self._session_scope = session_scope_factory
        self._registry = CapabilityRegistry()
        self._policy = ActionPolicy(self._registry)
        self._research = research_service or ResearchService(
            session_scope_factory=session_scope_factory
        )
        self._execution = ExecutionCoordinator(
            capability_registry=self._registry,
            executor_registry=CapabilityExecutorRegistry(
                (
                    ResearchSearchExecutor(self._research),
                    ResearchAnswerExecutor(
                        gateway=gateway,
                        research_service=self._research,
                        session_scope_factory=session_scope_factory,
                    ),
                )
            ),
            session_scope_factory=session_scope_factory,
        )

    async def answer(
        self,
        *,
        principal_id: UUID,
        request: ResearchEvidenceBundleRequest,
    ) -> GroundedResearchExecutionResponse:
        readiness: ModelProviderReadiness | None = None

        async def research_answer_available() -> bool:
            nonlocal readiness
            readiness = await self._gateway.readiness()
            return readiness.ready

        context = await ContextAssembler(
            session_scope_factory=self._session_scope,
            research_answer_availability=research_answer_available,
        ).assemble(
            principal_id=principal_id,
            request=ContextRequest(
                task=ContextTask.READER,
                library_entry_ids=request.library_entry_ids,
            ),
        )
        if not context.ai_enabled:
            raise AIDisabled("AI is disabled for this principal")
        if readiness is None or not readiness.configured:
            raise ModelProviderUnconfigured("No model provider is configured")
        if not readiness.ready:
            raise ModelProviderNotReady(readiness)

        step = PlanStep(
            step_id="research-answer-1",
            capability=CapabilityName.RESEARCH_ANSWER,
            arguments=request.model_dump(mode="json"),
            rationale=(
                "Answer the explicit reader question only from the canonical evidence bundle built "
                "from the selected owned books."
            ),
        )
        proposal = PlanProposal(
            summary="Produce one citation-grounded answer from canonical reader evidence.",
            steps=[step],
        )
        self._registry.validate_plan(proposal, context)
        decision = self._policy.evaluate(step, context)

        async with self._session_scope() as database_session:
            plan, stored_decisions = await PlanRepository(database_session).create(
                principal_id=principal_id,
                user_request=request.question,
                proposal=proposal,
                context=context,
                decisions=[decision],
            )
        stored_decision = stored_decisions[0]
        execution = await self._execution.execute(
            principal_id=principal_id,
            plan_id=plan.id,
            step_id=step.step_id,
        )
        output = GroundedResearchCapabilityOutput.model_validate(execution.output)
        return GroundedResearchExecutionResponse(
            plan_id=plan.id,
            action_decision_id=stored_decision.id,
            evidence=output.evidence,
            answer=output.answer,
            model_provider=output.model_provider,
            model_name=output.model_name,
            model_routing=output.model_routing,
        )
