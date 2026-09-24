import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry, CapabilityRisk
from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_domain import (
    DelegationAttemptClaim,
    DelegationAttemptClaimExpired,
    DelegationAttemptCompletion,
    DelegationAttemptPermit,
    DelegationBudgetExceeded,
    DelegationConflict,
    DelegationExecutionDisabled,
    DelegationResponse,
    DelegationStatus,
    DelegationStepUnavailable,
    DelegationStopRequested,
)
from bukmatika.ai.execution import (
    CapabilityExecutionResponse,
    CapabilityExecutorRegistry,
    ResearchSearchExecutor,
)
from bukmatika.ai.policy import POLICY_VERSION, ActionDecisionValue
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegations import DelegationRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.execution import ExecutionRepository, PlanIntegrityError

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
DEFAULT_CLAIM_LEASE_SECONDS = 60.0
DEFAULT_EXECUTION_TIMEOUT_SECONDS = 30.0
STOP_ERROR_CODE = "DELEGATION_STOPPED"
RUNTIME_BUDGET_ERROR_CODE = "runtime_budget_exhausted"
EXECUTION_TIMEOUT_ERROR_CODE = "DELEGATED_EXECUTION_TIMEOUT"


class DelegationRuntimeResult(BaseModel):
    claim: DelegationAttemptClaim
    execution: CapabilityExecutionResponse
    delegation: DelegationResponse


class DelegatedExecutionAdapter:
    """Delegated entrypoint into the canonical finite capability executors."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry | None = None,
        executor_registry: CapabilityExecutorRegistry | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._capabilities = capability_registry or CapabilityRegistry()
        self._executors = executor_registry or CapabilityExecutorRegistry(
            (ResearchSearchExecutor(),)
        )
        self._session_scope = session_scope_factory

    async def execute(
        self,
        *,
        principal_id: UUID,
        permit: DelegationAttemptPermit,
    ) -> CapabilityExecutionResponse:
        async with self._session_scope() as database_session:
            state = await ExecutionRepository(database_session).load(
                principal_id=principal_id,
                plan_id=permit.plan_id,
                step_id=permit.step_id,
            )

        if not state.ai_enabled or state.autonomy_level != 2:
            raise DelegationExecutionDisabled(
                "Delegated execution requires AI enabled with explicit autonomy Level 2"
            )
        if state.decision.decision != ActionDecisionValue.ALLOW.value:
            raise DelegationStepUnavailable(
                "Delegated execution requires a persisted allow decision"
            )
        if state.decision.policy_version != POLICY_VERSION:
            raise DelegationStepUnavailable("Delegated execution policy decision is stale")

        context = state.context.model_copy(
            update={
                "ai_enabled": state.ai_enabled,
                "learning_enabled": state.learning_enabled,
                "autonomy_level": state.autonomy_level,
            }
        )
        if not context.model_context_ready:
            raise DelegationExecutionDisabled("Delegated model context is not ready")
        if state.step.capability.value not in context.available_capabilities:
            raise DelegationStepUnavailable("Delegated capability is no longer available")

        spec = self._capabilities.get(state.step.capability)
        if not spec.delegatable:
            raise DelegationStepUnavailable(
                f"Capability is not enabled for delegation: {state.step.capability.value}"
            )
        if spec.risk is not CapabilityRisk.READ_ONLY:
            raise DelegationStepUnavailable("Only read-only capabilities are delegatable")
        if not spec.reversible or spec.undo_authority != "none_required":
            raise DelegationStepUnavailable("Delegated capability lacks a valid undo contract")

        executor = self._executors.get(state.step.capability)
        output = await executor.execute(
            principal_id=principal_id,
            action_decision_id=state.decision.id,
            arguments=state.step.arguments,
            context=context,
        )
        return CapabilityExecutionResponse(
            plan_id=permit.plan_id,
            step_id=permit.step_id,
            capability=state.step.capability,
            output=output.model_dump(mode="json"),
        )


class DelegationRuntimeService:
    """Consume at most one bounded delegated attempt per explicit invocation."""

    def __init__(
        self,
        *,
        control_service: DelegationControlService | None = None,
        execution_adapter: DelegatedExecutionAdapter | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
        claim_lease_seconds: float = DEFAULT_CLAIM_LEASE_SECONDS,
        execution_timeout_seconds: float = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    ) -> None:
        if claim_lease_seconds <= 0:
            raise ValueError("Delegation claim lease must be positive")
        if execution_timeout_seconds <= 0:
            raise ValueError("Delegation execution timeout must be positive")
        if execution_timeout_seconds >= claim_lease_seconds:
            raise ValueError("Delegation execution timeout must be shorter than its claim lease")
        self._session_scope = session_scope_factory
        self._control = control_service or DelegationControlService(
            session_scope_factory=session_scope_factory
        )
        self._execution = execution_adapter or DelegatedExecutionAdapter(
            session_scope_factory=session_scope_factory
        )
        self._claim_lease_seconds = claim_lease_seconds
        self._execution_timeout_seconds = execution_timeout_seconds

    async def run_once(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> DelegationRuntimeResult:
        permit = await self._active_or_authorize(
            principal_id=principal_id,
            delegation_id=delegation_id,
        )
        claim = await self.claim_permit(
            principal_id=principal_id,
            permit=permit,
        )
        try:
            async with asyncio.timeout(self._execution_timeout_seconds):
                execution = await self._execution.execute(
                    principal_id=principal_id,
                    permit=permit,
                )
        except TimeoutError:
            await self.settle_claim(
                principal_id=principal_id,
                claim=claim,
                completion=DelegationAttemptCompletion(
                    succeeded=False,
                    error_code=EXECUTION_TIMEOUT_ERROR_CODE,
                ),
            )
            raise
        except Exception as exc:
            error_code = str(getattr(exc, "code", type(exc).__name__))[:128]
            await self.settle_claim(
                principal_id=principal_id,
                claim=claim,
                completion=DelegationAttemptCompletion(
                    succeeded=False,
                    error_code=error_code,
                ),
            )
            raise

        delegation = await self.settle_claim(
            principal_id=principal_id,
            claim=claim,
            completion=DelegationAttemptCompletion(succeeded=True),
        )
        return DelegationRuntimeResult(
            claim=claim,
            execution=execution,
            delegation=delegation,
        )

    async def claim_permit(
        self,
        *,
        principal_id: UUID,
        permit: DelegationAttemptPermit,
    ) -> DelegationAttemptClaim:
        deferred_budget_error: DelegationBudgetExceeded | None = None
        claim: DelegationAttemptClaim | None = None
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=permit.delegation_id,
                lock=True,
            )
            if delegation.status == DelegationStatus.STOP_REQUESTED.value:
                raise DelegationStopRequested("Delegation stop has been requested")
            if delegation.status != DelegationStatus.RUNNING.value:
                raise DelegationConflict("Delegation is not running")
            if delegation.id != permit.delegation_id or delegation.plan_id != permit.plan_id:
                raise DelegationConflict("Delegation permit no longer matches its plan")
            if delegation.delegation_fingerprint != permit.delegation_fingerprint:
                raise DelegationConflict("Delegation permit fingerprint is stale")
            if delegation.started_at is None:
                raise PlanIntegrityError("Running delegation has no start time")

            attempt = await repository.active_attempt(
                principal_id=principal_id,
                delegation_id=delegation.id,
                lock=True,
            )
            if attempt is None or attempt.id != permit.attempt_id:
                raise DelegationConflict("Delegation permit is no longer active")
            if attempt.step_id != permit.step_id or attempt.attempt_number != permit.attempt_number:
                raise DelegationConflict("Delegation permit no longer matches its attempt")
            if delegation.current_step_index >= len(delegation.selected_step_ids):
                raise PlanIntegrityError("Delegation step index exceeds its bounded selection")
            if delegation.selected_step_ids[delegation.current_step_index] != permit.step_id:
                raise DelegationConflict("Delegation permit is not for the current bounded step")

            now = datetime.now(UTC)
            runtime_deadline = delegation.started_at + timedelta(
                seconds=delegation.max_runtime_seconds
            )
            required_window = timedelta(seconds=self._execution_timeout_seconds)
            if now + required_window > runtime_deadline:
                attempt.status = "failed"
                attempt.error_code = RUNTIME_BUDGET_ERROR_CODE
                attempt.finished_at = now
                self._clear_claim(attempt)
                delegation.status = DelegationStatus.FAILED.value
                delegation.failure_code = RUNTIME_BUDGET_ERROR_CODE
                delegation.completed_at = now
                await database_session.flush()
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.AI_DELEGATION_BUDGET_EXHAUSTED,
                    principal_id=principal_id,
                    entity_type="ai_delegation",
                    entity_id=delegation.id,
                    context={
                        "plan_id": str(delegation.plan_id),
                        "status": delegation.status,
                        "attempt_id": str(attempt.id),
                        "step_id": attempt.step_id,
                        "budget_code": RUNTIME_BUDGET_ERROR_CODE,
                    },
                )
                deferred_budget_error = DelegationBudgetExceeded(
                    "Delegation has insufficient runtime budget for another execution window"
                )
            else:
                reclaimed = attempt.claim_token is not None
                if (
                    attempt.claim_token is not None
                    and attempt.claim_expires_at is not None
                    and attempt.claim_expires_at > now
                ):
                    raise DelegationConflict("Delegated attempt already has an active runtime claim")
                token = uuid4()
                expires_at = now + timedelta(seconds=self._claim_lease_seconds)
                if expires_at > runtime_deadline:
                    expires_at = runtime_deadline
                attempt.claim_token = token
                attempt.claimed_at = now
                attempt.claim_expires_at = expires_at
                await database_session.flush()
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.AI_DELEGATION_ATTEMPT_CLAIMED,
                    principal_id=principal_id,
                    entity_type="ai_delegation",
                    entity_id=delegation.id,
                    context={
                        "plan_id": str(delegation.plan_id),
                        "attempt_id": str(attempt.id),
                        "step_id": attempt.step_id,
                        "attempt_number": attempt.attempt_number,
                        "claim_expires_at": expires_at.isoformat(),
                        "reclaimed": reclaimed,
                    },
                )
                claim = DelegationAttemptClaim(
                    permit=permit,
                    claim_token=token,
                    claimed_at=now,
                    claim_expires_at=expires_at,
                )
        if deferred_budget_error is not None:
            raise deferred_budget_error
        if claim is None:
            raise DelegationConflict("Delegation permit did not produce a runtime claim")
        return claim

    async def settle_claim(
        self,
        *,
        principal_id: UUID,
        claim: DelegationAttemptClaim,
        completion: DelegationAttemptCompletion,
    ) -> DelegationResponse:
        permit = claim.permit
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=permit.delegation_id,
                lock=True,
            )
            attempt = await repository.attempt_for_update(
                principal_id=principal_id,
                delegation_id=permit.delegation_id,
                attempt_id=permit.attempt_id,
                claim_token=claim.claim_token,
            )
            now = datetime.now(UTC)
            if attempt.status != "authorized":
                raise DelegationConflict("Delegated attempt is already final")
            if attempt.claim_expires_at is None or now > attempt.claim_expires_at:
                raise DelegationAttemptClaimExpired("Delegated runtime claim has expired")
            if delegation.status not in {
                DelegationStatus.RUNNING.value,
                DelegationStatus.STOP_REQUESTED.value,
            }:
                raise DelegationConflict("Delegation cannot accept attempt completion")
            if delegation.current_step_index >= len(delegation.selected_step_ids):
                raise PlanIntegrityError("Delegation step index exceeds its bounded selection")
            expected_step = delegation.selected_step_ids[delegation.current_step_index]
            if attempt.step_id != expected_step or attempt.step_id != permit.step_id:
                raise PlanIntegrityError("Delegated attempt no longer matches the current step")

            event_type = SemanticEventType.AI_DELEGATION_ATTEMPT_FAILED
            if completion.succeeded:
                attempt.status = "completed"
                attempt.finished_at = now
                delegation.current_step_index += 1
                event_type = SemanticEventType.AI_DELEGATION_ATTEMPT_COMPLETED
                if delegation.status == DelegationStatus.STOP_REQUESTED.value:
                    delegation.status = DelegationStatus.STOPPED.value
                    delegation.stopped_at = now
                elif delegation.current_step_index == len(delegation.selected_step_ids):
                    delegation.status = DelegationStatus.COMPLETED.value
                    delegation.completed_at = now
            else:
                attempt.status = "failed"
                attempt.error_code = completion.error_code
                attempt.finished_at = now
                if delegation.status == DelegationStatus.STOP_REQUESTED.value:
                    delegation.status = DelegationStatus.STOPPED.value
                    delegation.stopped_at = now
                else:
                    attempts = await repository.step_attempt_count(
                        principal_id=principal_id,
                        delegation_id=delegation.id,
                        step_id=attempt.step_id,
                    )
                    if attempts >= delegation.max_retries_per_step + 1:
                        delegation.status = DelegationStatus.FAILED.value
                        delegation.failure_code = completion.error_code
                        delegation.completed_at = now
            self._clear_claim(attempt)
            await database_session.flush()
            await InteractionEventRepository(database_session).record(
                event_type,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context={
                    "plan_id": str(delegation.plan_id),
                    "status": delegation.status,
                    "attempt_id": str(attempt.id),
                    "step_id": attempt.step_id,
                    "attempt_number": attempt.attempt_number,
                    "error_code": completion.error_code,
                },
            )
        return await self._control.get(
            principal_id=principal_id,
            delegation_id=permit.delegation_id,
        )

    async def acknowledge_stop(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> DelegationResponse:
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=delegation_id,
                lock=True,
            )
            if delegation.status == DelegationStatus.STOPPED.value:
                return await self._control.get(
                    principal_id=principal_id,
                    delegation_id=delegation_id,
                )
            if delegation.status != DelegationStatus.STOP_REQUESTED.value:
                raise DelegationConflict("Delegation is not awaiting stop acknowledgement")
            active = await repository.active_attempt(
                principal_id=principal_id,
                delegation_id=delegation_id,
                lock=True,
            )
            if active is not None:
                now = datetime.now(UTC)
                if (
                    active.claim_token is not None
                    and active.claim_expires_at is not None
                    and active.claim_expires_at > now
                ):
                    raise DelegationConflict("Active delegated execution must settle before stop")
                active.status = "cancelled"
                active.error_code = STOP_ERROR_CODE
                active.finished_at = now
                self._clear_claim(active)
                await database_session.flush()
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.AI_DELEGATION_ATTEMPT_CANCELLED,
                    principal_id=principal_id,
                    entity_type="ai_delegation",
                    entity_id=delegation.id,
                    context={
                        "plan_id": str(delegation.plan_id),
                        "attempt_id": str(active.id),
                        "step_id": active.step_id,
                        "attempt_number": active.attempt_number,
                        "error_code": STOP_ERROR_CODE,
                    },
                )
        return await self._control.acknowledge_stop(
            principal_id=principal_id,
            delegation_id=delegation_id,
        )

    async def _active_or_authorize(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> DelegationAttemptPermit:
        try:
            return await self._control.authorize_next_step(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
        except DelegationConflict:
            async with self._session_scope() as database_session:
                repository = DelegationRepository(database_session)
                delegation = await repository.get(
                    principal_id=principal_id,
                    delegation_id=delegation_id,
                )
                if delegation.status == DelegationStatus.STOP_REQUESTED.value:
                    raise DelegationStopRequested("Delegation stop has been requested") from None
                active = await repository.active_attempt(
                    principal_id=principal_id,
                    delegation_id=delegation_id,
                )
                if active is None:
                    raise
                return DelegationAttemptPermit(
                    attempt_id=active.id,
                    delegation_id=delegation.id,
                    plan_id=delegation.plan_id,
                    step_id=active.step_id,
                    attempt_number=active.attempt_number,
                    delegation_fingerprint=delegation.delegation_fingerprint,
                    authorized_at=active.authorized_at,
                )

    @staticmethod
    def _clear_claim(attempt: object) -> None:
        # The concrete ORM object is deliberately duck-typed here to keep claim clearing centralized.
        setattr(attempt, "claim_token", None)
        setattr(attempt, "claimed_at", None)
        setattr(attempt, "claim_expires_at", None)
