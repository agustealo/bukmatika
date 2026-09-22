from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
from bukmatika.persistence.models import InteractionEvent, Principal
from bukmatika.persistence.personalization import PreferenceClaimNotFound
from bukmatika.persistence.personalization_models import PreferenceClaim, UserModel
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PersonalizationSettingsUpdate,
    PreferenceKey,
    PreferenceScopeType,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"personalization-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


def _format_preference(value: str) -> ExplicitPreferenceRequest:
    return ExplicitPreferenceRequest(
        key=PreferenceKey.FORMAT_PREFERRED,
        value={"format": value},
    )


async def test_profile_creates_one_principal_owned_user_model(session: AsyncSession) -> None:
    principal = await _principal(session, "profile")
    service = PersonalizationService(session_scope_factory=_scope(session))

    first = await service.profile(principal_id=principal.id)
    second = await service.profile(principal_id=principal.id)

    assert first.user_model_id == second.user_model_id
    assert first.ai_enabled is True
    assert first.learning_enabled is True
    assert first.autonomy_level == 0
    assert first.active_preferences == []

    stored = await session.scalar(select(UserModel).where(UserModel.principal_id == principal.id))
    assert stored is not None
    assert stored.id == first.user_model_id


async def test_explicit_correction_supersedes_prior_claim_and_records_events(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "correction")
    service = PersonalizationService(session_scope_factory=_scope(session))

    first = await service.set_explicit_preference(
        principal_id=principal.id,
        request=_format_preference("PDF"),
    )
    second = await service.set_explicit_preference(
        principal_id=principal.id,
        request=_format_preference("EPUB"),
    )

    assert first.claim_id != second.claim_id
    assert second.source == "explicit"
    assert second.confidence == 1.0
    assert second.value == {"format": "EPUB"}

    claims = list(
        (
            await session.scalars(
                select(PreferenceClaim)
                .where(PreferenceClaim.principal_id == principal.id)
                .order_by(PreferenceClaim.created_at, PreferenceClaim.id)
            )
        ).all()
    )
    assert len(claims) == 2
    assert claims[0].status == "superseded"
    assert claims[1].status == "active"
    assert claims[1].value == {"format": "EPUB"}

    profile = await service.profile(principal_id=principal.id)
    assert [claim.claim_id for claim in profile.active_preferences] == [second.claim_id]

    events = list(
        (
            await session.scalars(
                select(InteractionEvent)
                .where(
                    InteractionEvent.principal_id == principal.id,
                    InteractionEvent.event_type == "personalization.preference_set",
                )
                .order_by(InteractionEvent.occurred_at, InteractionEvent.id)
            )
        ).all()
    )
    assert len(events) == 2
    assert events[-1].entity_id == second.claim_id
    assert events[-1].context["key"] == PreferenceKey.FORMAT_PREFERRED.value


async def test_forget_is_principal_scoped_and_preserves_deleted_audit_state(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    service = PersonalizationService(session_scope_factory=_scope(session))

    claim = await service.set_explicit_preference(
        principal_id=owner.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
            scope_type=PreferenceScopeType.RESEARCH,
            scope_value="historical-research",
        ),
    )

    with pytest.raises(PreferenceClaimNotFound):
        await service.forget_preference(principal_id=other.id, claim_id=claim.claim_id)

    owner_before = await service.profile(principal_id=owner.id)
    assert [item.claim_id for item in owner_before.active_preferences] == [claim.claim_id]

    await service.forget_preference(principal_id=owner.id, claim_id=claim.claim_id)

    owner_after = await service.profile(principal_id=owner.id)
    assert owner_after.active_preferences == []
    stored = await session.get(PreferenceClaim, claim.claim_id)
    assert stored is not None
    assert stored.status == "deleted"
    assert stored.deleted_at is not None

    forgotten = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == owner.id,
            InteractionEvent.event_type == "personalization.preference_forgotten",
            InteractionEvent.entity_id == claim.claim_id,
        )
    )
    assert forgotten is not None


async def test_personalization_profiles_do_not_leak_between_principals(
    session: AsyncSession,
) -> None:
    first = await _principal(session, "isolation-a")
    second = await _principal(session, "isolation-b")
    service = PersonalizationService(session_scope_factory=_scope(session))

    await service.set_explicit_preference(
        principal_id=first.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["maritime history"]},
        ),
    )

    first_profile = await service.profile(principal_id=first.id)
    second_profile = await service.profile(principal_id=second.id)

    assert len(first_profile.active_preferences) == 1
    assert second_profile.active_preferences == []
    assert first_profile.user_model_id != second_profile.user_model_id


async def test_settings_are_principal_scoped_and_emit_semantic_audit_event(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "settings")
    service = PersonalizationService(session_scope_factory=_scope(session))

    profile = await service.update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=False,
            autonomy_level=1,
        ),
    )

    assert profile.ai_enabled is False
    assert profile.learning_enabled is False
    assert profile.autonomy_level == 1

    event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "personalization.settings_updated",
        )
    )
    assert event is not None
    assert event.context == {
        "ai_enabled": False,
        "learning_enabled": False,
        "autonomy_level": 1,
    }


def test_autonomy_above_shipped_level_is_rejected_before_persistence() -> None:
    with pytest.raises(ValidationError):
        PersonalizationSettingsUpdate(
            ai_enabled=True,
            learning_enabled=True,
            autonomy_level=2,
        )


def test_personalization_routes_are_mounted() -> None:
    paths = {route.path for route in app.routes}
    assert "/v1/personalization" in paths
    assert "/v1/personalization/preferences" in paths
    assert "/v1/personalization/preferences/{claim_id}/forget" in paths
    assert "/v1/personalization/settings" in paths
