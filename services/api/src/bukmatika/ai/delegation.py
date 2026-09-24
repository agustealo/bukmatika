import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capabilities import CapabilityRegistry, CapabilityRisk
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationApprovalRequired,
    DelegationAttemptCompletion,
    DelegationAttemptPermit,
    DelegationBudgetExceeded,
    DelegationConflict,
    DelegationExecutionDisabled,
    DelegationInvalid,
    DelegationProposalRequest,
    DelegationResponse,
    DelegationStatus,
    DelegationStepUnavailable,
    DelegationStopRequested,
)
from bukmatika.ai.domain import PlanStep
from bukmatika.ai.policy import ActionDecisionValue, POLICY_VERSION
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegation_models import AIDelegation, AIDelegationApproval
from bukmatika.persistence.delegations import DelegationPlanBundle, DelegationRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.execution import ExecutionRepository, PlanIntegrityError
from bukmatika.persistence.personalization_models import UserModel
from bukmatika.personalization.domain import ContextManifest

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
DELEGATION_POLICY_VERSION = "ai-delegation-policy-v1"


class DelegationControlService:
    """Durable bounded-delegation control plane. It never executes a capability."""

    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._registry = registry or CapabilityRegistry()
        self._session_scope = session_scope_factory

    async def propose(
        self,
        *,
        principal_id: UUID,
        plan_id: UUID,
        request: DelegationProposalRequest,
    ) -> DelegationResponse:
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            bundle = await repository.plan_bundle(principal_id=principal_id, plan_id=plan_id)
            steps, context = self._validate_plan_bundle(bundle)
            selected = self._selected_steps(steps=steps, step_ids=request.step_ids)
            self._validate_selected_steps(bundle=bundle, context=context, selected=selected)

            user_model = await database_session.scalar(
                select(UserModel).where(UserModel.principal_id == principal_id)
            )
            if user_model is None or not user_model.ai_enabled:
                raise DelegationExecutionDisabled("AI is disabled for this principal")

            plan_fingerprint = _plan_fingerprint(bundle)
            delegation_fingerprint = _delegation_fingerprint(
                plan_fingerprint=plan_fingerprint,
                request=request,
            )
            delegation = await repository.create(
                principal_id=principal_id,
                plan_id=plan_id,
                selected_step_ids=request.step_ids,
                plan_fingerprint=plan_fingerprint,
                delegation_fingerprint=delegation_fingerprint,
                max_runtime_seconds=request.max_runtime_seconds,
                max_retries_per_step=request.max_retries_per_step,
                max_total_attempts=request.max_total_attempts,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.AI_DELEGATION_PROPOSED,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context=_event_context(delegation),
            )
            return _response(delegation, approval=None)

    async def get(
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
            )
            approval = await repository.approval(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
            return _response(delegation, approval=approval)

    async def decide(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
        request: DelegationApprovalRequest,
    ) -> DelegationResponse:
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=delegation_id,
                lock=True,
            )
            existing = await repository.approval(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
            if existing is not None:
                if existing.decision != request.decision.value:
                    raise DelegationConflict("Delegation approval decision is already final")
                if existing.delegation_fingerprint != delegation.delegation_fingerprint:
                    raise DelegationConflict("Stored delegation approval fingerprint is stale")
                return _response(delegation, approval=existing)
            if delegation.status != DelegationStatus.PROPOSED.value:
                raise DelegationConflict("Delegation is no longer awaiting approval")

            approval = await repository.create_approval(
                delegation=delegation,
                decision=request.decision.value,
            )
            delegation.status = (
                DelegationStatus.APPROVED.value
                if request.decision is DelegationApprovalDecision.APPROVED
                else DelegationStatus.REJECTED.value
            )
            await database_session.flush()
            await InteractionEventRepository(database_session).record(
                SemanticEventType.AI_DELEGATION_DECIDED,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context={**_event_context(delegation), "decision": request.decision.value},
            )
            return _response(delegation, approval=approval)

    async def request_stop(
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
            approval = await repository.approval(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
            now = datetime.now(UTC)
            if delegation.status in {
                DelegationStatus.PROPOSED.value,
                DelegationStatus.APPROVED.value,
            }:
                delegation.status = DelegationStatus.CANCELLED.value
                delegation.stopped_at = now
            elif delegation.status == DelegationStatus.RUNNING.value:
                delegation.status = DelegationStatus.STOP_REQUESTED.value
                delegation.stop_requested_at = now
            elif delegation.status in {
                DelegationStatus.STOP_REQUESTED.value,
                DelegationStatus.STOPPED.value,
                DelegationStatus.CANCELLED.value,
            }:
                return _response(delegation, approval=approval)
            else:
                raise DelegationConflict("Terminal delegation cannot be stopped")
            await database_session.flush()
            await InteractionEventRepository(database_session).record(
                SemanticEventType.AI_DELEGATION_STOP_REQUESTED,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context=_event_context(delegation),
            )
            return _response(delegation, approval=approval)

    async def activate(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> DelegationResponse:
        """Internal boundary. Public settings cannot currently select autonomy Level 2."""
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=delegation_id,
                lock=True,
            )
            approval = await repository.approval(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
            self._require_approval(delegation, approval)
            if delegation.status != DelegationStatus.APPROVED.value:
                raise DelegationConflict("Delegation is not ready to activate")
            await self._revalidate_contract(repository=repository, delegation=delegation)
            user_model = await _current_user_model(database_session, principal_id)
            if not user_model.ai_enabled or user_model.autonomy_level != 2:
                raise DelegationExecutionDisabled(
                    "Delegation requires AI enabled with explicit autonomy Level 2"
                )
            delegation.status = DelegationStatus.RUNNING.value
            delegation.started_at = datetime.now(UTC)
            await database_session.flush()
            await InteractionEventRepository(database_session).record(
                SemanticEventType.AI_DELEGATION_STARTED,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context=_event_context(delegation),
            )
            return _response(delegation, approval=approval)

    async def authorize_next_step(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> DelegationAttemptPermit:
        """Issue one durable attempt permit only. This method never invokes an executor."""
        deferred_error: DelegationBudgetExceeded | None = None
        permit: DelegationAttemptPermit | None = None
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=delegation_id,
                lock=True,
            )
            if delegation.status == DelegationStatus.STOP_REQUESTED.value:
                raise DelegationStopRequested("Delegation stop has been requested")
            if delegation.status != DelegationStatus.RUNNING.value:
                raise DelegationConflict("Delegation is not running")
            if await repository.active_attempt(
                principal_id=principal_id,
                delegation_id=delegation_id,
            ) is not None:
                raise DelegationConflict("Delegation already has an active attempt permit")

            if _runtime_budget_expired(delegation):
                await self._fail_budget(database_session, delegation, "runtime_budget_exhausted")
                deferred_error = DelegationBudgetExceeded(
                    "Delegation runtime budget is exhausted"
                )
            elif delegation.attempts_used >= delegation.max_total_attempts:
                await self._fail_budget(database_session, delegation, "attempt_budget_exhausted")
                deferred_error = DelegationBudgetExceeded(
                    "Delegation attempt budget is exhausted"
                )
            elif delegation.current_step_index >= len(delegation.selected_step_ids):
                raise DelegationConflict("Delegation has no remaining step to authorize")
            else:
                await self._revalidate_contract(repository=repository, delegation=delegation)
                step_id = delegation.selected_step_ids[delegation.current_step_index]
                state = await ExecutionRepository(database_session).load(
                    principal_id=principal_id,
                    plan_id=delegation.plan_id,
                    step_id=step_id,
                )
                if not state.ai_enabled or state.autonomy_level != 2:
                    raise DelegationExecutionDisabled(
                        "Delegation requires AI enabled with explicit autonomy Level 2"
                    )
                if state.decision.decision != ActionDecisionValue.ALLOW.value:
                    raise DelegationStepUnavailable(
                        "Delegated step is not backed by a persisted allow decision"
                    )
                if state.decision.policy_version != POLICY_VERSION:
                    raise DelegationStepUnavailable("Delegated step policy decision is stale")
                self._validate_delegatable_step(state.step)
                context = state.context.model_copy(
                    update={
                        "ai_enabled": state.ai_enabled,
                        "learning_enabled": state.learning_enabled,
                        "autonomy_level": state.autonomy_level,
                    }
                )
                self._require_delegation_policy(state.step, context)

                previous_attempts = await repository.step_attempt_count(
                    principal_id=principal_id,
                    delegation_id=delegation_id,
                    step_id=step_id,
                )
                if previous_attempts >= delegation.max_retries_per_step + 1:
                    await self._fail_budget(
                        database_session,
                        delegation,
                        "retry_budget_exhausted",
                    )
                    deferred_error = DelegationBudgetExceeded(
                        "Delegation retry budget is exhausted"
                    )
                else:
                    attempt = await repository.create_attempt(
                        delegation=delegation,
                        step_id=step_id,
                        attempt_number=previous_attempts + 1,
                    )
                    await InteractionEventRepository(database_session).record(
                        SemanticEventType.AI_DELEGATION_ATTEMPT_AUTHORIZED,
                        principal_id=principal_id,
                        entity_type="ai_delegation",
                        entity_id=delegation.id,
                        context={
                            **_event_context(delegation),
                            "attempt_id": str(attempt.id),
                            "step_id": step_id,
                            "attempt_number": attempt.attempt_number,
                        },
                    )
                    permit = DelegationAttemptPermit(
                        attempt_id=attempt.id,
                        delegation_id=delegation.id,
                        plan_id=delegation.plan_id,
                        step_id=step_id,
                        attempt_number=attempt.attempt_number,
                        delegation_fingerprint=delegation.delegation_fingerprint,
                        authorized_at=attempt.authorized_at,
                    )
        if deferred_error is not None:
            raise deferred_error
        if permit is None:
            raise DelegationConflict("Delegation did not produce an attempt permit")
        return permit

    async def complete_attempt(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
        attempt_id: UUID,
        completion: DelegationAttemptCompletion,
    ) -> DelegationResponse:
        async with self._session_scope() as database_session:
            repository = DelegationRepository(database_session)
            delegation = await repository.get(
                principal_id=principal_id,
                delegation_id=delegation_id,
                lock=True,
            )
            attempt = await repository.attempt_for_update(
                principal_id=principal_id,
                delegation_id=delegation_id,
                attempt_id=attempt_id,
            )
            if attempt.status != "authorized":
                raise DelegationConflict("Delegated attempt is already final")
            if delegation.status not in {
                DelegationStatus.RUNNING.value,
                DelegationStatus.STOP_REQUESTED.value,
            }:
                raise DelegationConflict("Delegation cannot accept attempt completion")
            if delegation.current_step_index >= len(delegation.selected_step_ids):
                raise PlanIntegrityError("Delegation step index exceeds its bounded selection")
            expected_step = delegation.selected_step_ids[delegation.current_step_index]
            if attempt.step_id != expected_step:
                raise PlanIntegrityError("Delegated attempt no longer matches the current step")

            now = datetime.now(UTC)
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
                        delegation_id=delegation_id,
                        step_id=attempt.step_id,
                    )
                    if attempts >= delegation.max_retries_per_step + 1:
                        delegation.status = DelegationStatus.FAILED.value
                        delegation.failure_code = completion.error_code
                        delegation.completed_at = now
            await database_session.flush()
            await InteractionEventRepository(database_session).record(
                event_type,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context={
                    **_event_context(delegation),
                    "attempt_id": str(attempt.id),
                    "step_id": attempt.step_id,
                    "attempt_number": attempt.attempt_number,
                    "error_code": completion.error_code,
                },
            )
            approval = await repository.approval(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
            return _response(delegation, approval=approval)

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
                approval = await repository.approval(
                    principal_id=principal_id,
                    delegation_id=delegation_id,
                )
                return _response(delegation, approval=approval)
            if delegation.status != DelegationStatus.STOP_REQUESTED.value:
                raise DelegationConflict("Delegation is not awaiting stop acknowledgement")
            if await repository.active_attempt(
                principal_id=principal_id,
                delegation_id=delegation_id,
            ) is not None:
                raise DelegationConflict("Active delegated attempt must settle before stop")
            delegation.status = DelegationStatus.STOPPED.value
            delegation.stopped_at = datetime.now(UTC)
            await database_session.flush()
            await InteractionEventRepository(database_session).record(
                SemanticEventType.AI_DELEGATION_STOPPED,
                principal_id=principal_id,
                entity_type="ai_delegation",
                entity_id=delegation.id,
                context=_event_context(delegation),
            )
            approval = await repository.approval(
                principal_id=principal_id,
                delegation_id=delegation_id,
            )
            return _response(delegation, approval=approval)

    def _validate_plan_bundle(
        self,
        bundle: DelegationPlanBundle,
    ) -> tuple[list[PlanStep], ContextManifest]:
        if bundle.plan.status not in {"proposed", "approved"}:
            raise DelegationInvalid("Only proposed or approved plans can be delegated")
        try:
            steps = [PlanStep.model_validate(value) for value in bundle.plan.steps]
            context = ContextManifest.model_validate(bundle.plan.context_manifest)
        except ValidationError as exc:
            raise DelegationInvalid("Persisted plan context or steps are invalid") from exc
        if len(bundle.decisions) != len(steps):
            raise DelegationInvalid("Persisted plan decision count does not match its steps")
        return steps, context

    def _selected_steps(self, *, steps: list[PlanStep], step_ids: list[str]) -> list[PlanStep]:
        selected_ids = set(step_ids)
        selected = [step for step in steps if step.step_id in selected_ids]
        if len(selected) != len(step_ids):
            raise DelegationInvalid("Delegation references a step outside the persisted plan")
        if [step.step_id for step in selected] != step_ids:
            raise DelegationInvalid("Delegation step IDs must preserve persisted plan order")
        return selected

    def _validate_selected_steps(
        self,
        *,
        bundle: DelegationPlanBundle,
        context: ContextManifest,
        selected: list[PlanStep],
    ) -> None:
        decisions = {decision.step_id: decision for decision in bundle.decisions}
        for step in selected:
            decision = decisions.get(step.step_id)
            if decision is None or decision.capability != step.capability.value:
                raise DelegationInvalid("Delegated step lacks a matching persisted decision")
            if decision.decision != ActionDecisionValue.ALLOW.value:
                raise DelegationInvalid("Only persisted allow decisions can enter delegation")
            if decision.policy_version != POLICY_VERSION:
                raise DelegationInvalid("Delegated step policy decision is stale")
            if step.capability.value not in context.available_capabilities:
                raise DelegationInvalid("Delegated capability is absent from persisted context")
            self._validate_delegatable_step(step)

    def _validate_delegatable_step(self, step: PlanStep) -> None:
        spec = self._registry.get(step.capability)
        if not spec.delegatable:
            raise DelegationStepUnavailable(
                f"Capability is not enabled for delegation: {step.capability.value}"
            )
        if spec.risk is not CapabilityRisk.READ_ONLY:
            raise DelegationStepUnavailable("Only read-only capabilities are delegatable")
        if not spec.reversible or spec.undo_authority != "none_required":
            raise DelegationStepUnavailable("Delegated capability lacks a valid undo contract")

    def _require_delegation_policy(self, step: PlanStep, context: ContextManifest) -> None:
        if not context.ai_enabled or context.autonomy_level != 2:
            raise DelegationExecutionDisabled("Delegation policy requires autonomy Level 2")
        if step.capability.value not in context.available_capabilities:
            raise DelegationStepUnavailable("Delegated capability is no longer available")
        self._validate_delegatable_step(step)

    async def _revalidate_contract(
        self,
        *,
        repository: DelegationRepository,
        delegation: AIDelegation,
    ) -> None:
        bundle = await repository.plan_bundle(
            principal_id=delegation.principal_id,
            plan_id=delegation.plan_id,
        )
        steps, context = self._validate_plan_bundle(bundle)
        selected = self._selected_steps(
            steps=steps,
            step_ids=list(delegation.selected_step_ids),
        )
        self._validate_selected_steps(bundle=bundle, context=context, selected=selected)
        current_plan_fingerprint = _plan_fingerprint(bundle)
        if current_plan_fingerprint != delegation.plan_fingerprint:
            raise DelegationConflict("Persisted plan changed after delegation proposal")
        request = DelegationProposalRequest(
            step_ids=list(delegation.selected_step_ids),
            max_runtime_seconds=delegation.max_runtime_seconds,
            max_retries_per_step=delegation.max_retries_per_step,
            max_total_attempts=delegation.max_total_attempts,
        )
        if (
            _delegation_fingerprint(
                plan_fingerprint=current_plan_fingerprint,
                request=request,
            )
            != delegation.delegation_fingerprint
        ):
            raise DelegationConflict("Delegation budget or selection fingerprint is invalid")

    def _require_approval(
        self,
        delegation: AIDelegation,
        approval: AIDelegationApproval | None,
    ) -> None:
        if approval is None or approval.decision != DelegationApprovalDecision.APPROVED.value:
            raise DelegationApprovalRequired("Delegation requires exact user approval")
        if approval.delegation_fingerprint != delegation.delegation_fingerprint:
            raise DelegationConflict("Delegation approval fingerprint no longer matches")

    async def _fail_budget(
        self,
        database_session: AsyncSession,
        delegation: AIDelegation,
        code: str,
    ) -> None:
        delegation.status = DelegationStatus.FAILED.value
        delegation.failure_code = code
        delegation.completed_at = datetime.now(UTC)
        await database_session.flush()
        await InteractionEventRepository(database_session).record(
            SemanticEventType.AI_DELEGATION_BUDGET_EXHAUSTED,
            principal_id=delegation.principal_id,
            entity_type="ai_delegation",
            entity_id=delegation.id,
            context={**_event_context(delegation), "budget_code": code},
        )


async def _current_user_model(session: AsyncSession, principal_id: UUID) -> UserModel:
    user_model = await session.scalar(
        select(UserModel).where(UserModel.principal_id == principal_id).with_for_update()
    )
    if user_model is None:
        raise PlanIntegrityError("Principal has no durable user model")
    return user_model


def _runtime_budget_expired(delegation: AIDelegation) -> bool:
    if delegation.started_at is None:
        raise DelegationConflict("Running delegation has no start time")
    deadline = delegation.started_at + timedelta(seconds=delegation.max_runtime_seconds)
    return datetime.now(UTC) > deadline


def _plan_fingerprint(bundle: DelegationPlanBundle) -> str:
    decision_by_step = {decision.step_id: decision for decision in bundle.decisions}
    decisions: list[dict[str, str]] = []
    for raw_step in bundle.plan.steps:
        step = PlanStep.model_validate(raw_step)
        decision = decision_by_step.get(step.step_id)
        if decision is None:
            raise DelegationInvalid("Persisted plan step has no action decision")
        decisions.append(
            {
                "id": str(decision.id),
                "step_id": decision.step_id,
                "capability": decision.capability,
                "decision": decision.decision,
                "policy_version": decision.policy_version,
            }
        )
    return _digest(
        {
            "principal_id": str(bundle.plan.principal_id),
            "plan_id": str(bundle.plan.id),
            "planner_version": bundle.plan.planner_version,
            "user_request": bundle.plan.user_request,
            "steps": bundle.plan.steps,
            "context_manifest": bundle.plan.context_manifest,
            "decisions": decisions,
        }
    )


def _delegation_fingerprint(
    *,
    plan_fingerprint: str,
    request: DelegationProposalRequest,
) -> str:
    return _digest(
        {
            "delegation_policy_version": DELEGATION_POLICY_VERSION,
            "plan_fingerprint": plan_fingerprint,
            "step_ids": request.step_ids,
            "max_runtime_seconds": request.max_runtime_seconds,
            "max_retries_per_step": request.max_retries_per_step,
            "max_total_attempts": request.max_total_attempts,
        }
    )


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _event_context(delegation: AIDelegation) -> dict[str, object]:
    return {
        "plan_id": str(delegation.plan_id),
        "status": delegation.status,
        "step_ids": list(delegation.selected_step_ids),
        "delegation_fingerprint": delegation.delegation_fingerprint,
        "delegation_policy_version": DELEGATION_POLICY_VERSION,
        "max_runtime_seconds": delegation.max_runtime_seconds,
        "max_retries_per_step": delegation.max_retries_per_step,
        "max_total_attempts": delegation.max_total_attempts,
        "attempts_used": delegation.attempts_used,
        "current_step_index": delegation.current_step_index,
    }


def _response(
    delegation: AIDelegation,
    *,
    approval: AIDelegationApproval | None,
) -> DelegationResponse:
    return DelegationResponse(
        delegation_id=delegation.id,
        principal_id=delegation.principal_id,
        plan_id=delegation.plan_id,
        status=DelegationStatus(delegation.status),
        step_ids=list(delegation.selected_step_ids),
        plan_fingerprint=delegation.plan_fingerprint,
        delegation_fingerprint=delegation.delegation_fingerprint,
        max_runtime_seconds=delegation.max_runtime_seconds,
        max_retries_per_step=delegation.max_retries_per_step,
        max_total_attempts=delegation.max_total_attempts,
        attempts_used=delegation.attempts_used,
        current_step_index=delegation.current_step_index,
        approval_decision=(
            DelegationApprovalDecision(approval.decision) if approval is not None else None
        ),
        created_at=delegation.created_at,
        updated_at=delegation.updated_at,
        started_at=delegation.started_at,
        stop_requested_at=delegation.stop_requested_at,
        stopped_at=delegation.stopped_at,
        completed_at=delegation.completed_at,
        failure_code=delegation.failure_code,
    )
