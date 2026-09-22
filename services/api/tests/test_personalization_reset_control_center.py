from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.personalization.control import PersonalizationControlService
from bukmatika.personalization.domain import ExplicitPreferenceRequest, PreferenceKey
from bukmatika.personalization.portability import PersonalizationPortabilityService
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_control_center_is_fresh_immediately_after_reset(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="reset-control-center")
    session.add(principal)
    await session.flush()
    scope = _scope(session)

    personalization = PersonalizationService(session_scope_factory=scope)
    before = await personalization.profile(principal_id=principal.id)
    await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
        ),
    )

    reset = await PersonalizationPortabilityService(session_scope_factory=scope).reset(
        principal_id=principal.id
    )
    snapshot = await PersonalizationControlService(session_scope_factory=scope).snapshot(
        principal_id=principal.id
    )

    assert reset.user_model_id != before.user_model_id
    assert snapshot.user_model_id == reset.user_model_id
    assert snapshot.ai_enabled is True
    assert snapshot.learning_enabled is True
    assert snapshot.autonomy_level == 0
    assert snapshot.explicit_preferences == []
    assert snapshot.inferred_preferences == []
    assert snapshot.active_goals == []
    assert snapshot.recent_activity == []
    assert snapshot.recent_outcomes == []
