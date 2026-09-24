from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_control_domain import (
    ActiveDelegationControlItem,
    DelegationConsentAction,
    DelegationConsentConflict,
    DelegationConsentRequest,
    DelegationConsentScope,
    DelegationConsentUnavailable,
    DelegationControlStatusResponse,
)
from bukmatika.ai.delegation_domain import DelegationResponse, DelegationStatus
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegation_control import DelegationControlRepository
from bukmatika.persistence.delegation_control_models import AIDelegationConsent
from bukmatika.persistence.delegation_models import AIDelegation
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.personalization import PersonalizationRepository, lock_personalization_state
from bukmatika.persistence.personalization_models import UserModel

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
LEVEL2_DELEGATION_CONSENT_POLICY_VERSION = "ai-level2-read-only-consent-v1"


class DelegationOperatorControlService:
    """Principal-visible Level 2 consent/status surface. It never schedules work."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
        delegation_service: DelegationControlService | None = None,
    ) -> None:
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
        state = await self.status(principal_id=principal_id)
        if not state.level2_enabled:
            raise DelegationConsentUnavailable(
                "Level 2 read-only delegation consent is not active"
            )
        return await self._delegation_service.activate(
            principal_id=principal_id,
            delegation_id=delegation_id,
        )

    async def _grant(self, *, principal_id: UUID) -> DelegationControlStatusResponse:
        async with self._session_scope() as database_session:
            await lock_personalization_state(database_session, principal_id)
            personalization = PersonalizationRepository(database_session)
            await personalization.get_or_create_user_model(principal_id)
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

            repository = DelegationControlRepository(database_session)
            consent = await repository.consent(principal_id=principal_id, lock=True)
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
            user_model = await database_session.scalar(
                select(UserModel)
                .where(UserModel.principal_id == principal_id)
                .with_for_update()
            )
            if user_model is None:
                raise DelegationConsentConflict("Principal user model is unavailable")
            repository = DelegationControlRepository(database_session)
            consent = await repository.consent(principal_id=principal_id, lock=True)
            if user_model.autonomy_level == 2:
                user_model.autonomy_level = (
                    consent.prior_autonomy_level
                    if consent is not None and consent.status == "active"
                    else 1
                )
                user_model.updated_at = datetime.now(UTC)
            await revoke_level2_consent_in_session(
                database_session,
                principal_id=principal_id,
                reason=reason,
            )
            return await _status_in_session(database_session, principal_id=principal_id)


async def revoke_level2_consent_in_session(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    reason: str,
) -> None:
    """Revoke Level 2 consent and stop nonterminal delegation work in the caller transaction."""
    repository = DelegationControlRepository(database_session)
    previous = await repository.consent(principal_id=principal_id, lock=True)
    was_active = previous is not None and previous.status == "active"
    consent, changed = await repository.revoke_consent_and_stop_active(
        principal_id=principal_id,
        reason=reason,
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
    "DelegationOperatorControlService",
    "LEVEL2_DELEGATION_CONSENT_POLICY_VERSION",
    "revoke_level2_consent_in_session",
]
