from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry, CapabilityRisk
from bukmatika.ai.domain import CapabilityName
from bukmatika.ai.policy import ActionDecisionValue, ActionPolicy
from bukmatika.persistence import session_scope
from bukmatika.persistence.execution import ExecutionRepository, PlanIntegrityError
from bukmatika.personalization.domain import ContextManifest
from bukmatika.research import ResearchSearchRequest, ResearchService

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class AIExecutionDisabled(RuntimeError):
    code = "AI_EXECUTION_DISABLED"


class ActionApprovalRequired(RuntimeError):
    code = "ACTION_APPROVAL_REQUIRED"


class ActionExecutionDenied(RuntimeError):
    code = "ACTION_EXECUTION_DENIED"


class CapabilityExecutorUnavailable(RuntimeError):
    code = "CAPABILITY_EXECUTOR_UNAVAILABLE"


class CapabilityArgumentsInvalid(ValueError):
    code = "CAPABILITY_ARGUMENTS_INVALID"


class CapabilityExecutor(Protocol):
    capability: CapabilityName

    async def execute(
        self,
        *,
        principal_id: UUID,
        arguments: dict[str, JsonValue],
    ) -> BaseModel: ...


class ResearchSearchExecutor:
    capability = CapabilityName.RESEARCH_SEARCH

    def __init__(self, service: ResearchService | None = None) -> None:
        self._service = service or ResearchService()

    async def execute(
        self,
        *,
        principal_id: UUID,
        arguments: dict[str, JsonValue],
    ) -> BaseModel:
        try:
            request = ResearchSearchRequest.model_validate(arguments)
        except ValidationError as exc:
            raise CapabilityArgumentsInvalid("Invalid research.search arguments") from exc
        return await self._service.search(principal_id=principal_id, request=request)


class CapabilityExecutorRegistry:
    """Finite registry of actually executable AI capabilities."""

    def __init__(self, executors: tuple[CapabilityExecutor, ...] | None = None) -> None:
        values = executors or (ResearchSearchExecutor(),)
        self._executors = {executor.capability: executor for executor in values}
        if len(self._executors) != len(values):
            raise ValueError("Capability executor names must be unique")

    def get(self, capability: CapabilityName) -> CapabilityExecutor:
        try:
            return self._executors[capability]
        except KeyError as exc:
            raise CapabilityExecutorUnavailable(
                f"No executor is registered for capability: {capability.value}"
            ) from exc


class CapabilityExecutionResponse(BaseModel):
    plan_id: UUID
    step_id: str
    capability: CapabilityName
    output: JsonValue


class ExecutionCoordinator:
    """Executes only currently allowed, principal-owned read-only planned actions."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        executor_registry: CapabilityExecutorRegistry | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._capabilities = capability_registry or CapabilityRegistry()
        self._executors = executor_registry or CapabilityExecutorRegistry()
        self._session_scope = session_scope_factory
        self._policy = ActionPolicy(self._capabilities)

    async def execute(
        self,
        *,
        principal_id: UUID,
        plan_id: UUID,
        step_id: str,
    ) -> CapabilityExecutionResponse:
        async with self._session_scope() as database_session:
            state = await ExecutionRepository(database_session).load(
                principal_id=principal_id,
                plan_id=plan_id,
                step_id=step_id,
            )

        if not state.ai_enabled:
            raise AIExecutionDisabled("AI execution is disabled for this principal")

        context = state.context.model_copy(
            update={
                "ai_enabled": state.ai_enabled,
                "learning_enabled": state.learning_enabled,
                "autonomy_level": state.autonomy_level,
            }
        )
        spec = self._capabilities.get(state.step.capability)
        if state.step.capability.value not in context.available_capabilities:
            raise ActionExecutionDenied("Capability is unavailable in the persisted context")
        if spec.risk is not CapabilityRisk.READ_ONLY:
            raise ActionApprovalRequired("Only read-only capabilities execute at Levels 0-1")

        try:
            persisted_decision = ActionDecisionValue(state.decision.decision)
        except ValueError as exc:
            raise PlanIntegrityError("Persisted action decision value is invalid") from exc
        if persisted_decision is ActionDecisionValue.REQUIRE_APPROVAL:
            raise ActionApprovalRequired("Persisted action decision requires user approval")
        if persisted_decision is not ActionDecisionValue.ALLOW:
            raise ActionExecutionDenied("Persisted action decision does not allow execution")

        current_decision = self._policy.evaluate(state.step, context)
        if current_decision.decision is ActionDecisionValue.REQUIRE_APPROVAL:
            raise ActionApprovalRequired("Current action policy requires user approval")
        if current_decision.decision is not ActionDecisionValue.ALLOW:
            raise ActionExecutionDenied("Current action policy denies execution")

        executor = self._executors.get(state.step.capability)
        output = await executor.execute(
            principal_id=principal_id,
            arguments=state.step.arguments,
        )
        return CapabilityExecutionResponse(
            plan_id=plan_id,
            step_id=step_id,
            capability=state.step.capability,
            output=output.model_dump(mode="json"),
        )
