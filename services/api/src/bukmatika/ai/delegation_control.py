from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.delegation import DelegationControlService, _event_context, _response
from bukmatika.ai.delegation_control_domain import (
    ActiveDelegationControlItem,
    DelegationConsentAction,
    DelegationConsentConflict,
    DelegationConsentRequest,
    DelegationConsentScope,
    DelegationConsentUnavailable,
    DelegationControlStatusResponse,
)
from bukmatika.ai.delegation_domain import (
    DelegationConflict,
    DelegationExecutionDisabled,
    DelegationResponse,
    DelegationStatus,
)
from bukmatika.ai.delegation_jobs import enqueue_delegation_job_in_session
from bukmatika.config import Settings, get_settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegation_control import DelegationControlRepository
from bukmatika.persistence.delegation_models import AIDelegation
from bukmatika.persistence.delegations import DelegationRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.personalization import (
    PersonalizationRepository,
    lock_personalization_state,
)
from bukmatika.persistence.personalization_models import UserModel

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
LEVEL2_DELEGATION_CONSENT_POLICY_VERSION = "ai-level2-read-only-consent-v1"


class DelegationOperatorControlService:
    """Principal-visible Level 2 consent/status and explicit durable-start surface."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
        delegation_service: DelegationControlService | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_scope = session_scope_factory
        self._delegation_service = delegation_service or DelegationControlService(
            session_scope_factory=session_scope_factory
        )

    async def status(self, *, principal_id: UUID) -> DelegationControlStatusResponse:
        async with self._session_scope() as database_session:
            return await _status_in_session(database_session, principal_id=principal_id)

    async def decide_consent(
        self,
        *,
        principal_id: UUID,
        request: DelegationConsentRequest,
    ) -> DelegationControlStatusResponse:
        if request.action is DelegationConsentAction.GRANT:
            return await self._grant(principal_id=principal_id)
        return await self._revoke(principal_id=principal_id, reason="explicit_revoke")

    async def start(
        self,
        *,
        principal_id: UUID,
        delegation_id: UUID,
    ) -> DelegationResponse:
        async with self._session_scope() as database_session:
            await lock_personalization_state(database_session, principal_id)
            personalization = PersonalizationRepository(database_session)
            await personalization.get_or_create_user_model(principal_id)

            consent_repository = DelegationControlRepository(database_session)
            consent = await consent_repository.consent(principal_id=principal_id, lock=True)
            if (
                consent is None
                or consent.status != "active"
                or consent.scope != DelegationConsentScope.READ_ONLY.value
                or consent.policy_version != LEVEL2_DELEGATION_CONSENT_POLICY_VERSION
            ):
                raise DelegationConsentUnavailable(
                    "Level 2 read-only delegation consent is not active"
                )

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
            self._delegation_service._require_approval(delegation, approval)
            if delegation.status not in {
                DelegationStatus.APPROVED.value,
                DelegationStatus.RUNNING.value,
            }:
                raise DelegationConflict("Delegation is not ready to start")

            await self._delegation_service._revalidate_contract(
                repository=repository,
                delegation=delegation,
            )
            user_model = await database_session.scalar(
                select(UserModel)
                .where(UserModel.principal_id == principal_id)
                .with_for_update()
            )
            if user_model is None:
                raise DelegationConsentConflict("Principal user model is unavailable")
            if not user_model.ai_enabled or user_model.autonomy_level != 2:
                raise DelegationExecutionDisabled(
                    "Delegation requires AI enabled with explicit autonomy Level 2"
                )

            if delegation.status == DelegationStatus.APPROVED.value:
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
            elif delegation.started_at is None:
                raise DelegationConflict("Running delegation has no durable start time")

            await enqueue_delegation_job_in_session(
                database_session,
                settings=self._settings,
                principal_id=principal_id,
                delegation_id=delegation.id,
            )
            return _response(delegation, approval=approval)

    async def _grant(self, *, principal_id: UUID) -> DelegationControlStatusResponse:
        async with self._session_scope() as database_session:
            await lock_personalization_state(database_session, principal_id)
            personalization = PersonalizationRepository(database_session)
            await personalization.get_or_create_user_model(principal_id)
            repository = DelegationControlRepository(database_session)
            consent = await repository.consent(principal_id=principal_id, lock=True)
            user_model = await database_session.scalar(
                select(UserModel)
                .where(UserModel.principal_id == principal_id)
                .with_for_update()
            )
            if user_model is None:
                raise DelegationConsentConflict("Principal user model is unavailable")
            if not user_model.ai_enabled:
                raise DelegationConsentUnavailable(
                    "AI must be enabled before Level 2 delegation consent can be granted"
                )

            if consent is not None and consent.status == "active":
                if (
                    consent.policy_version == LEVEL2_DELEGATION_CONSENT_POLICY_VERSION
                    and user_model.autonomy_level == 2
                ):
                    return await _status_in_session(database_session, principal_id=principal_id)
                if user_model.autonomy_level != 2:
                    raise DelegationConsentConflict(
                        "Active delegation consent conflicts with the current autonomy level"
                    )
                consent.policy_version = LEVEL2_DELEGATION_CONSENT_POLICY_VERSION
                consent.consented_at = datetime.now(UTC)
                consent.revoked_at = None
            else:
                if user_model.autonomy_level > 1:
                    raise DelegationConsentConflict(
                        "Autonomy Level 2 exists without active delegation consent"
                    )
                prior_level = user_model.autonomy_level
                if consent is None:
                    consent = await repository.create_consent(
                        principal_id=principal_id,
                        policy_version=LEVEL2_DELEGATION_CONSENT_POLICY_VERSION,
                        prior_autonomy_level=prior_level,
                    )
                else:
                    consent.status = "active"
                    consent.scope = DelegationConsentScope.READ_ONLY.value
                    consent.policy_version = LEVEL2_DELEGATION_CONSENT_POLICY_VERSION
                    consent.prior_autonomy_level = prior_level
                    consent.consented_at = datetime.now(UTC)
                    consent.revoked_at = None
                user_model.autonomy_level = 2
                user_model.updated_at = datetime.now(UTC)
                await database_session.flush()

            await InteractionEventRepository(database_session).record(
                SemanticEventType.AI_DELEGATION_CONSENT_GRANTED,
                principal_id=principal_id,
                entity_type="ai_delegation_consent",
                entity_id=consent.id,
                context={
                    "scope": consent.scope,
                    "policy_version": consent.policy_version,
                    "autonomy_level": user_model.autonomy_level,
                },
            )
            return await _status_in_session(database_session, principal_id=principal_id)

    async def _revoke(
        self,
        *,
        principal_id: UUID,
        reason: str,
    ) -> DelegationControlStatusResponse:
        async with self._session_scope() as database_session:
            await lock_personalization_state(database_session, principal_id)
            personalization = PersonalizationRepository(database_session)
            await personalization.get_or_create_user_model(principal_id)
            prior_level = await revoke_level2_consent_in_session(
                database_session,
                principal_id=principal_id,
                reason=reason,
            )
            user_model = await database_session.scalar(
                select(UserModel)
                .where(UserModel.principal_id == principal_id)
                .with_for_update()
            )
            if user_model is None:
                raise DelegationConsentConflict("Principal user model is unavailable")
            if user_model.autonomy_level == 2:
                user_model.autonomy_level = prior_level if prior_level is not None else 1
                user_model.updated_at = datetime.now(UTC)
                await database_session.flush()
            return await _status_in_session(database_session, principal_id=principal_id)


async def revoke_level2_consent_in_session(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    reason: str,
) -> int | None:
    """Revoke Level 2 consent and stop nonterminal delegation work in the caller transaction."""
    repository = DelegationControlRepository(database_session)
    previous = await repository.consent(principal_id=principal_id, lock=True)
    was_active = previous is not None and previous.status == "active"
    prior_level = previous.prior_autonomy_level if was_active and previous is not None else None
    consent, changed = await repository.revoke_consent_and_stop_active(
        principal_id=principal_id
    )
    events = InteractionEventRepository(database_session)
    if was_active and consent is not None:
        await events.record(
            SemanticEventType.AI_DELEGATION_CONSENT_REVOKED,
            principal_id=principal_id,
            entity_type="ai_delegation_consent",
            entity_id=consent.id,
            context={
                "scope": consent.scope,
                "policy_version": consent.policy_version,
                "reason": reason,
            },
        )
    for delegation in changed:
        await events.record(
            SemanticEventType.AI_DELEGATION_STOP_REQUESTED,
            principal_id=principal_id,
            entity_type="ai_delegation",
            entity_id=delegation.id,
            context={
                "delegation_id": str(delegation.id),
                "status": delegation.status,
                "reason": reason,
            },
        )
    return prior_level


async def _status_in_session(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
) -> DelegationControlStatusResponse:
    personalization = PersonalizationRepository(database_session)
    user_model = await personalization.get_or_create_user_model(principal_id)
    repository = DelegationControlRepository(database_session)
    consent = await repository.consent(principal_id=principal_id)
    active = await repository.active_delegations(principal_id=principal_id)
    level2_enabled = bool(
        user_model.ai_enabled
        and user_model.autonomy_level == 2
        and consent is not None
        and consent.status == "active"
        and consent.scope == DelegationConsentScope.READ_ONLY.value
        and consent.policy_version == LEVEL2_DELEGATION_CONSENT_POLICY_VERSION
    )
    return DelegationControlStatusResponse(
        ai_enabled=user_model.ai_enabled,
        autonomy_level=user_model.autonomy_level,
        level2_enabled=level2_enabled,
        consent_scope=(
            DelegationConsentScope(consent.scope)
            if consent is not None and consent.status == "active"
            else None
        ),
        consent_policy_version=(consent.policy_version if consent is not None else None),
        consented_at=(consent.consented_at if consent is not None else None),
        revoked_at=(consent.revoked_at if consent is not None else None),
        active_delegations=[_delegation_item(value) for value in active],
    )


def _delegation_item(delegation: AIDelegation) -> ActiveDelegationControlItem:
    remaining_runtime = delegation.max_runtime_seconds
    if delegation.started_at is not None:
        elapsed = max(0, int((datetime.now(UTC) - delegation.started_at).total_seconds()))
        remaining_runtime = max(0, delegation.max_runtime_seconds - elapsed)
    remaining_steps = max(0, len(delegation.selected_step_ids) - delegation.current_step_index)
    return ActiveDelegationControlItem(
        delegation_id=delegation.id,
        plan_id=delegation.plan_id,
        status=DelegationStatus(delegation.status),
        step_ids=list(delegation.selected_step_ids),
        current_step_index=delegation.current_step_index,
        remaining_steps=remaining_steps,
        attempts_used=delegation.attempts_used,
        max_total_attempts=delegation.max_total_attempts,
        remaining_attempts=max(0, delegation.max_total_attempts - delegation.attempts_used),
        max_runtime_seconds=delegation.max_runtime_seconds,
        remaining_runtime_seconds=remaining_runtime,
        started_at=delegation.started_at,
        stop_requested_at=delegation.stop_requested_at,
    )


__all__ = [
    "LEVEL2_DELEGATION_CONSENT_POLICY_VERSION",
    "DelegationOperatorControlService",
    "revoke_level2_consent_in_session",
]
