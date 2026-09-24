import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_delegation import _approve, _plan, _principal, _proposal, _scope, _step

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_control import DelegationOperatorControlService
from bukmatika.ai.delegation_control_domain import (
    DelegationConsentAction,
    DelegationConsentRequest,
    DelegationConsentScope,
    DelegationConsentUnavailable,
)
from bukmatika.ai.delegation_domain import DelegationStatus
from bukmatika.ai.domain import CapabilityName
from bukmatika.main import app
from bukmatika.persistence.delegation_control_models import AIDelegationConsent
from bukmatika.persistence.delegation_models import AIDelegation
from bukmatika.persistence.personalization_models import UserModel
from bukmatika.personalization.domain import PersonalizationSettingsUpdate
from bukmatika.personalization.portability import PersonalizationPortabilityService
from bukmatika.personalization.service import PersonalizationService


async def test_level_two_requires_explicit_delegation_consent(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "level2-consent")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(control, principal_id=principal.id, delegation_id=proposed.delegation_id)

    with pytest.raises(DelegationConsentUnavailable):
        await operator.start(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )

    granted = await operator.decide_consent(
        principal_id=principal.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
    )
    assert granted.level2_enabled is True
    assert granted.autonomy_level == 2
    assert granted.consent_scope is DelegationConsentScope.READ_ONLY

    running = await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert running.status is DelegationStatus.RUNNING

    status = await operator.status(principal_id=principal.id)
    assert len(status.active_delegations) == 1
    item = status.active_delegations[0]
    assert item.delegation_id == proposed.delegation_id
    assert item.status is DelegationStatus.RUNNING
    assert item.remaining_steps == 1
    assert item.remaining_attempts == 1
    assert 0 < item.remaining_runtime_seconds <= item.max_runtime_seconds


async def test_level_two_consent_is_principal_scoped(session: AsyncSession) -> None:
    owner = await _principal(session, "level2-owner")
    other = await _principal(session, "level2-other")
    operator = DelegationOperatorControlService(session_scope_factory=_scope(session))

    await operator.decide_consent(
        principal_id=owner.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
    )

    owner_status = await operator.status(principal_id=owner.id)
    other_status = await operator.status(principal_id=other.id)
    assert owner_status.level2_enabled is True
    assert owner_status.autonomy_level == 2
    assert other_status.level2_enabled is False
    assert other_status.autonomy_level == 0


async def test_explicit_revocation_restores_prior_level_and_requests_stop(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "level2-revoke")
    personalization = PersonalizationService(session_scope_factory=_scope(session))
    await personalization.update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=True,
            learning_enabled=True,
            autonomy_level=1,
        ),
    )
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(control, principal_id=principal.id, delegation_id=proposed.delegation_id)
    await operator.decide_consent(
        principal_id=principal.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
    )
    await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )

    revoked = await operator.decide_consent(
        principal_id=principal.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.REVOKE),
    )
    assert revoked.level2_enabled is False
    assert revoked.autonomy_level == 1
    assert len(revoked.active_delegations) == 1
    assert revoked.active_delegations[0].status is DelegationStatus.STOP_REQUESTED

    delegation = await session.get(AIDelegation, proposed.delegation_id)
    assert delegation is not None
    assert delegation.status == DelegationStatus.STOP_REQUESTED.value
    assert delegation.stop_requested_at is not None
    consent = await session.scalar(
        select(AIDelegationConsent).where(AIDelegationConsent.principal_id == principal.id)
    )
    assert consent is not None
    assert consent.status == "revoked"
    assert consent.revoked_at is not None


async def test_disabling_ai_revokes_consent_and_cancels_not_started_delegation(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "level2-ai-off")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(control, principal_id=principal.id, delegation_id=proposed.delegation_id)
    await operator.decide_consent(
        principal_id=principal.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
    )

    personalization = PersonalizationService(session_scope_factory=_scope(session))
    profile = await personalization.update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=True,
            autonomy_level=1,
        ),
    )
    assert profile.ai_enabled is False
    assert profile.autonomy_level == 1

    status = await operator.status(principal_id=principal.id)
    assert status.level2_enabled is False
    assert status.active_delegations == []
    delegation = await session.get(AIDelegation, proposed.delegation_id)
    assert delegation is not None
    assert delegation.status == DelegationStatus.CANCELLED.value
    assert delegation.stopped_at is not None


async def test_level_zero_settings_edit_does_not_cancel_unconsented_proposal(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "level0-settings")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )

    personalization = PersonalizationService(session_scope_factory=_scope(session))
    await personalization.update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=True,
            learning_enabled=False,
            autonomy_level=0,
        ),
    )

    unchanged = await control.get(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert unchanged.status is DelegationStatus.PROPOSED


async def test_personalization_reset_destroys_level_two_consent_and_delegation_state(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "level2-reset")
    plan = await _plan(
        session,
        principal_id=principal.id,
        steps=[_step(CapabilityName.RESEARCH_SEARCH)],
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=_proposal("research"),
    )
    await _approve(control, principal_id=principal.id, delegation_id=proposed.delegation_id)
    await operator.decide_consent(
        principal_id=principal.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
    )
    await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )

    reset = await PersonalizationPortabilityService(
        session_scope_factory=_scope(session)
    ).reset(principal_id=principal.id)

    assert reset.ai_enabled is True
    assert reset.autonomy_level == 0
    assert await session.scalar(
        select(func.count(AIDelegationConsent.id)).where(
            AIDelegationConsent.principal_id == principal.id
        )
    ) == 0
    assert await session.scalar(
        select(func.count(AIDelegation.id)).where(AIDelegation.principal_id == principal.id)
    ) == 0


def test_generic_settings_cannot_grant_level_two() -> None:
    with pytest.raises(ValidationError):
        PersonalizationSettingsUpdate(
            ai_enabled=True,
            learning_enabled=True,
            autonomy_level=2,
        )


def test_level_two_control_routes_are_explicit_and_no_scheduler_surface_exists() -> None:
    paths = set(app.openapi()["paths"])
    assert "/v1/personalization/delegation-control" in paths
    assert "/v1/personalization/delegation-control/consent" in paths
    assert "/v1/personalization/delegations/{delegation_id}/start" in paths
    assert "/v1/ai/delegations/{delegation_id}/stop" in paths
    assert "/v1/personalization/delegations/{delegation_id}/run" not in paths
    assert "/v1/personalization/delegations/{delegation_id}/schedule" not in paths
    assert "/v1/personalization/delegation-worker" not in paths


async def test_explicit_consent_requires_ai_enabled(session: AsyncSession) -> None:
    principal = await _principal(session, "level2-disabled")
    user_model = await session.scalar(
        select(UserModel).where(UserModel.principal_id == principal.id)
    )
    assert user_model is not None
    user_model.ai_enabled = False
    await session.flush()
    operator = DelegationOperatorControlService(session_scope_factory=_scope(session))

    with pytest.raises(DelegationConsentUnavailable):
        await operator.decide_consent(
            principal_id=principal.id,
            request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
        )
