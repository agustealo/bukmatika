from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.approval_domain import (
    ActionApprovalInvalid,
    ActionApprovalRejected,
    ActionExecutionReceiptInvalid,
    action_step_fingerprint,
)
from bukmatika.ai.capabilities import CapabilityRegistry, CapabilityRisk
from bukmatika.ai.domain import CapabilityName
from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelGateway,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelProviderUnconfigured,
    ModelRequest,
    ModelTask,
    UnconfiguredModelGateway,
)
from bukmatika.ai.policy import ActionDecisionValue, ActionPolicy
from bukmatika.ai.research_domain import GroundedResearchCapabilityOutput
from bukmatika.persistence import session_scope
from bukmatika.persistence.approvals import ApprovalRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.execution import ExecutionRepository, PlanIntegrityError
from bukmatika.persistence.personalization import lock_personalization_state
from bukmatika.persistence.personalization_models import UserModel
from bukmatika.personalization.domain import (
    ContextManifest,
    ExplicitPreferenceRequest,
    PreferenceClaimResponse,
)
from bukmatika.personalization.service import set_explicit_preference_in_session
from bukmatika.research import (
    GroundedResearchAnswer,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceItem,
    ResearchEvidenceReferenceInvalid,
    ResearchSearchRequest,
    ResearchService,
)

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
        action_decision_id: UUID,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel: ...


class ResearchSearchExecutor:
    capability = CapabilityName.RESEARCH_SEARCH

    def __init__(self, service: ResearchService | None = None) -> None:
        self._service = service or ResearchService()

    async def execute(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel:
        del action_decision_id
        try:
            request = ResearchSearchRequest.model_validate(arguments)
        except ValidationError as exc:
            raise CapabilityArgumentsInvalid("Invalid research.search arguments") from exc

        selected_entry_ids = {entry.library_entry_id for entry in context.library_entries}
        requested_entry_ids = set(request.library_entry_ids)
        if not requested_entry_ids.issubset(selected_entry_ids):
            raise CapabilityArgumentsInvalid(
                "research.search library entries must be selected in the persisted context"
            )

        return await self._service.search(principal_id=principal_id, request=request)


class PreferenceProposalExecutor:
    capability = CapabilityName.PREFERENCES_PROPOSE

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def execute(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel:
        del arguments, context
        async with self._session_scope() as database_session:
            repository = ApprovalRepository(database_session)
            target = await repository.target(
                principal_id=principal_id,
                action_decision_id=action_decision_id,
                lock=True,
            )
            if target.step.capability is not self.capability:
                raise ActionApprovalInvalid("Approved step is not a preference proposal")

            await lock_personalization_state(database_session, principal_id)
            user_model = await database_session.scalar(
                select(UserModel)
                .where(UserModel.principal_id == principal_id)
                .with_for_update()
            )
            if user_model is None or not user_model.ai_enabled:
                raise AIExecutionDisabled("AI execution is disabled for this principal")

            fingerprint = action_step_fingerprint(
                plan_id=target.plan.id,
                action_decision_id=target.decision.id,
                step=target.step,
            )
            approval = await repository.approval_for_execution(
                principal_id=principal_id,
                action_decision_id=action_decision_id,
            )
            if approval is None:
                raise ActionApprovalRequired("Action requires explicit user approval")
            if approval.decision != "approved":
                raise ActionApprovalRejected("User rejected this action")
            if approval.step_fingerprint != fingerprint:
                raise ActionApprovalInvalid("Approved action no longer matches the persisted step")

            receipt = await repository.execution_receipt(
                principal_id=principal_id,
                action_decision_id=action_decision_id,
            )
            if receipt is not None:
                if (
                    receipt.step_fingerprint != fingerprint
                    or receipt.capability != self.capability.value
                ):
                    raise ActionExecutionReceiptInvalid(
                        "Execution receipt no longer matches the approved action"
                    )
                try:
                    return PreferenceClaimResponse.model_validate(receipt.output)
                except ValidationError as exc:
                    raise ActionExecutionReceiptInvalid(
                        "Stored execution receipt output is invalid"
                    ) from exc

            try:
                request = ExplicitPreferenceRequest.model_validate(target.step.arguments)
            except ValidationError as exc:
                raise CapabilityArgumentsInvalid("Invalid preferences.propose arguments") from exc

            response = await set_explicit_preference_in_session(
                database_session,
                principal_id=principal_id,
                request=request,
            )
            output = response.model_dump(mode="json")
            await repository.record_execution(
                target=target,
                principal_id=principal_id,
                step_fingerprint=fingerprint,
                output=output,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACTION_EXECUTED,
                principal_id=principal_id,
                entity_type="action_decision",
                entity_id=action_decision_id,
                context={
                    "plan_id": str(target.plan.id),
                    "step_id": target.step.step_id,
                    "capability": self.capability.value,
                    "step_fingerprint": fingerprint,
                },
            )
            return response


class ResearchAnswerExecutor:
    capability = CapabilityName.RESEARCH_ANSWER

    def __init__(
        self,
        *,
        gateway: ModelGateway | None = None,
        research_service: ResearchService | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._gateway = gateway or UnconfiguredModelGateway()
        self._research = research_service or ResearchService(
            session_scope_factory=session_scope_factory
        )
        self._session_scope = session_scope_factory

    async def execute(
        self,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel:
        try:
            request = ResearchEvidenceBundleRequest.model_validate(arguments)
        except ValidationError as exc:
            raise CapabilityArgumentsInvalid("Invalid research.answer arguments") from exc

        selected_entry_ids = {entry.library_entry_id for entry in context.library_entries}
        requested_entry_ids = set(request.library_entry_ids)
        if not requested_entry_ids.issubset(selected_entry_ids):
            raise CapabilityArgumentsInvalid(
                "research.answer library entries must be selected in the persisted context"
            )

        identity = self._gateway.identity
        if identity is None:
            raise ModelProviderUnconfigured("No model provider is configured")

        bundle = await self._research.evidence_bundle(
            principal_id=principal_id,
            request=request,
        )
        model_request = ModelRequest(
            task=ModelTask.RESEARCH_ANSWER,
            payload={
                "question": bundle.question,
                "evidence": [_model_evidence(item) for item in bundle.evidence],
            },
            data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
            max_output_tokens=2_048,
            timeout_seconds=45,
        )
        try:
            answer = await self._gateway.generate_structured(
                model_request,
                GroundedResearchAnswer,
            )
            answer = self._research.validate_grounded_answer(bundle=bundle, answer=answer)
        except (
            ModelProviderRequestFailed,
            ModelProviderResponseInvalid,
            ResearchEvidenceReferenceInvalid,
        ) as exc:
            await self._record_model_event(
                SemanticEventType.AI_MODEL_FAILED,
                principal_id=principal_id,
                action_decision_id=action_decision_id,
                provider=identity.provider,
                model=identity.model,
                routing=identity.routing,
                evidence_count=len(bundle.evidence),
                error_code=getattr(exc, "code", type(exc).__name__),
            )
            raise

        await self._record_model_event(
            SemanticEventType.AI_MODEL_COMPLETED,
            principal_id=principal_id,
            action_decision_id=action_decision_id,
            provider=identity.provider,
            model=identity.model,
            routing=identity.routing,
            evidence_count=len(bundle.evidence),
        )
        return GroundedResearchCapabilityOutput(
            evidence=bundle,
            answer=answer,
            model_provider=identity.provider,
            model_name=identity.model,
            model_routing=identity.routing,
        )

    async def _record_model_event(
        self,
        event_type: SemanticEventType,
        *,
        principal_id: UUID,
        action_decision_id: UUID,
        provider: str,
        model: str,
        routing: str,
        evidence_count: int,
        error_code: str | None = None,
    ) -> None:
        context: dict[str, JsonValue] = {
            "task": ModelTask.RESEARCH_ANSWER.value,
            "provider": provider,
            "model": model,
            "routing": routing,
            "evidence_count": evidence_count,
        }
        if error_code is not None:
            context["error_code"] = error_code
        async with self._session_scope() as database_session:
            await InteractionEventRepository(database_session).record(
                event_type,
                principal_id=principal_id,
                entity_type="action_decision",
                entity_id=action_decision_id,
                context=context,
            )


def _model_evidence(item: ResearchEvidenceItem) -> dict[str, JsonValue]:
    return {
        "evidence_id": item.evidence_id,
        "work_title": item.work_title,
        "edition_title": item.edition_title,
        "heading": item.heading,
        "locator": item.locator,
        "char_start": item.char_start,
        "char_end": item.char_end,
        "text": item.text,
    }


class CapabilityExecutorRegistry:
    """Finite registry of actually executable AI capabilities."""

    def __init__(self, executors: tuple[CapabilityExecutor, ...] | None = None) -> None:
        values = executors or (
            ResearchSearchExecutor(),
            PreferenceProposalExecutor(),
        )
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
    """Executes only currently allowed principal-owned planned actions."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        executor_registry: CapabilityExecutorRegistry | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._capabilities = capability_registry or CapabilityRegistry()
        self._executors = executor_registry or CapabilityExecutorRegistry(
            (
                ResearchSearchExecutor(),
                PreferenceProposalExecutor(session_scope_factory=session_scope_factory),
            )
        )
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
            approval = await ApprovalRepository(database_session).approval_for_execution(
                principal_id=principal_id,
                action_decision_id=state.decision.id,
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

        try:
            persisted_decision = ActionDecisionValue(state.decision.decision)
        except ValueError as exc:
            raise PlanIntegrityError("Persisted action decision value is invalid") from exc

        current_decision = self._policy.evaluate(state.step, context)
        if current_decision.decision is ActionDecisionValue.DENY:
            raise ActionExecutionDenied("Current action policy denies execution")

        if spec.risk is CapabilityRisk.READ_ONLY:
            if persisted_decision is ActionDecisionValue.REQUIRE_APPROVAL:
                raise ActionApprovalRequired("Persisted action decision requires user approval")
            if persisted_decision is not ActionDecisionValue.ALLOW:
                raise ActionExecutionDenied("Persisted action decision does not allow execution")
            if current_decision.decision is ActionDecisionValue.REQUIRE_APPROVAL:
                raise ActionApprovalRequired("Current action policy requires user approval")
            if current_decision.decision is not ActionDecisionValue.ALLOW:
                raise ActionExecutionDenied("Current action policy does not allow execution")
        else:
            if persisted_decision is not ActionDecisionValue.REQUIRE_APPROVAL:
                raise ActionExecutionDenied("Durable action lacks the required approval gate")
            if approval is None:
                raise ActionApprovalRequired("Action requires explicit user approval")
            if approval.decision != "approved":
                raise ActionApprovalRejected("User rejected this action")
            expected_fingerprint = action_step_fingerprint(
                plan_id=state.plan_id,
                action_decision_id=state.decision.id,
                step=state.step,
            )
            if approval.step_fingerprint != expected_fingerprint:
                raise ActionApprovalInvalid("Approved action no longer matches the persisted step")
            if current_decision.decision not in {
                ActionDecisionValue.ALLOW,
                ActionDecisionValue.REQUIRE_APPROVAL,
            }:
                raise ActionExecutionDenied("Current action policy does not permit execution")
            if spec.risk is CapabilityRisk.CONSEQUENTIAL:
                raise ActionExecutionDenied("Consequential actions are not executable in this slice")
            if state.step.capability is not CapabilityName.PREFERENCES_PROPOSE:
                raise ActionExecutionDenied("Only reversible preference proposals are executable")
            if not spec.reversible:
                raise ActionExecutionDenied("Approved action is not reversible")

        executor = self._executors.get(state.step.capability)
        output = await executor.execute(
            principal_id=principal_id,
            action_decision_id=state.decision.id,
            arguments=state.step.arguments,
            context=context,
        )
        return CapabilityExecutionResponse(
            plan_id=plan_id,
            step_id=step_id,
            capability=state.step.capability,
            output=output.model_dump(mode="json"),
        )
