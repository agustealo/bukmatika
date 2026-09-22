from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.personalization import (
    PersonalizationRepository,
    PreferenceClaimNotFound,
)
from bukmatika.persistence.personalization_models import PreferenceClaim, UserModel
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PersonalizationProfileResponse,
    PersonalizationSettingsUpdate,
    PreferenceClaimResponse,
    PreferenceInfluence,
    PreferenceKey,
    PreferenceScopeType,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PersonalizationService:
    """Principal-scoped authority for explicit personalization state."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def profile(self, *, principal_id: UUID) -> PersonalizationProfileResponse:
        async with self._session_scope() as database_session:
            repository = PersonalizationRepository(database_session)
            user_model = await repository.get_or_create_user_model(principal_id)
            claims = await repository.active_claims(principal_id)
            return _profile_response(user_model, claims)

    async def set_explicit_preference(
        self,
        *,
        principal_id: UUID,
        request: ExplicitPreferenceRequest,
    ) -> PreferenceClaimResponse:
        async with self._session_scope() as database_session:
            repository = PersonalizationRepository(database_session)
            claim = await repository.set_explicit_preference(
                principal_id=principal_id,
                request=request,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.PREFERENCE_SET,
                principal_id=principal_id,
                entity_type="preference_claim",
                entity_id=claim.id,
                context={
                    "key": claim.key,
                    "scope_type": claim.scope_type,
                    "scope_value": claim.scope_value,
                    "source": claim.source,
                },
            )
            return _claim_response(claim)

    async def forget_preference(
        self,
        *,
        principal_id: UUID,
        claim_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            repository = PersonalizationRepository(database_session)
            claim = await repository.forget_claim(principal_id=principal_id, claim_id=claim_id)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.PREFERENCE_FORGOTTEN,
                principal_id=principal_id,
                entity_type="preference_claim",
                entity_id=claim.id,
                context={
                    "key": claim.key,
                    "scope_type": claim.scope_type,
                    "scope_value": claim.scope_value,
                },
            )

    async def update_settings(
        self,
        *,
        principal_id: UUID,
        update: PersonalizationSettingsUpdate,
    ) -> PersonalizationProfileResponse:
        async with self._session_scope() as database_session:
            repository = PersonalizationRepository(database_session)
            user_model = await repository.update_settings(
                principal_id=principal_id,
                update=update,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.PERSONALIZATION_SETTINGS_UPDATED,
                principal_id=principal_id,
                entity_type="user_model",
                entity_id=user_model.id,
                context={
                    "ai_enabled": user_model.ai_enabled,
                    "learning_enabled": user_model.learning_enabled,
                    "autonomy_level": user_model.autonomy_level,
                },
            )
            claims = await repository.active_claims(principal_id)
            return _profile_response(user_model, claims)


def _profile_response(
    user_model: UserModel,
    claims: list[PreferenceClaim],
) -> PersonalizationProfileResponse:
    return PersonalizationProfileResponse(
        user_model_id=user_model.id,
        ai_enabled=user_model.ai_enabled,
        learning_enabled=user_model.learning_enabled,
        autonomy_level=user_model.autonomy_level,
        active_preferences=[_claim_response(claim) for claim in claims],
    )


def _claim_response(claim: PreferenceClaim) -> PreferenceClaimResponse:
    return PreferenceClaimResponse(
        claim_id=claim.id,
        key=PreferenceKey(claim.key),
        value=dict(claim.value),
        source=claim.source,
        status=claim.status,
        confidence=claim.confidence,
        scope_type=PreferenceScopeType(claim.scope_type),
        scope_value=claim.scope_value,
        influence=PreferenceInfluence.model_validate(claim.influence),
        evidence_count=claim.evidence_count,
        first_observed_at=claim.first_observed_at,
        last_reinforced_at=claim.last_reinforced_at,
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


__all__ = ["PersonalizationService", "PreferenceClaimNotFound"]
