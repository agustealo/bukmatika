from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.personalization_models import PreferenceClaim, UserModel
from bukmatika.personalization.domain import ExplicitPreferenceRequest, PersonalizationSettingsUpdate


class PreferenceClaimNotFound(LookupError):
    pass


class PersonalizationRepository:
    """Canonical user-model and preference-claim persistence authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_user_model(self, principal_id: UUID) -> UserModel:
        statement = (
            insert(UserModel)
            .values(
                principal_id=principal_id,
                ai_enabled=True,
                learning_enabled=True,
                autonomy_level=0,
            )
            .on_conflict_do_nothing(constraint="uq_user_model_principal")
            .returning(UserModel)
        )
        created = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self._session.scalar(
            select(UserModel).where(UserModel.principal_id == principal_id)
        )
        if existing is None:
            raise RuntimeError("UserModel upsert returned no row")
        return existing

    async def active_claims(self, principal_id: UUID) -> list[PreferenceClaim]:
        values = await self._session.scalars(
            select(PreferenceClaim)
            .where(
                PreferenceClaim.principal_id == principal_id,
                PreferenceClaim.status == "active",
            )
            .order_by(
                PreferenceClaim.scope_type,
                PreferenceClaim.scope_value,
                PreferenceClaim.key,
                PreferenceClaim.created_at,
            )
        )
        return list(values)

    async def set_explicit_preference(
        self,
        *,
        principal_id: UUID,
        request: ExplicitPreferenceRequest,
    ) -> PreferenceClaim:
        user_model = await self._lock_user_model(principal_id)
        existing = list(
            (
                await self._session.scalars(
                    select(PreferenceClaim)
                    .where(
                        PreferenceClaim.principal_id == principal_id,
                        PreferenceClaim.key == request.key.value,
                        PreferenceClaim.scope_type == request.scope_type.value,
                        PreferenceClaim.scope_value == request.scope_value,
                        PreferenceClaim.status == "active",
                    )
                    .with_for_update()
                )
            ).all()
        )
        now = datetime.now(UTC)
        for claim in existing:
            claim.status = "superseded"
            claim.updated_at = now
        if existing:
            await self._session.flush()

        claim = PreferenceClaim(
            user_model_id=user_model.id,
            principal_id=principal_id,
            key=request.key.value,
            value=request.value,
            source="explicit",
            status="active",
            confidence=1.0,
            scope_type=request.scope_type.value,
            scope_value=request.scope_value,
            evidence_count=0,
            first_observed_at=now,
            last_reinforced_at=now,
            decay_half_life_days=None,
            influence=request.influence.model_dump(),
        )
        self._session.add(claim)
        await self._session.flush()
        return claim

    async def forget_claim(self, *, principal_id: UUID, claim_id: UUID) -> PreferenceClaim:
        await self._lock_user_model(principal_id)
        claim = await self._session.scalar(
            select(PreferenceClaim)
            .where(
                PreferenceClaim.id == claim_id,
                PreferenceClaim.principal_id == principal_id,
                PreferenceClaim.status == "active",
            )
            .with_for_update()
        )
        if claim is None:
            raise PreferenceClaimNotFound("Active preference claim does not exist")
        now = datetime.now(UTC)
        claim.status = "deleted"
        claim.deleted_at = now
        claim.updated_at = now
        await self._session.flush()
        return claim

    async def update_settings(
        self,
        *,
        principal_id: UUID,
        update: PersonalizationSettingsUpdate,
    ) -> UserModel:
        user_model = await self._lock_user_model(principal_id)
        user_model.ai_enabled = update.ai_enabled
        user_model.learning_enabled = update.learning_enabled
        user_model.autonomy_level = update.autonomy_level
        user_model.updated_at = datetime.now(UTC)
        await self._session.flush()
        return user_model

    async def _lock_user_model(self, principal_id: UUID) -> UserModel:
        await self.get_or_create_user_model(principal_id)
        user_model = await self._session.scalar(
            select(UserModel)
            .where(UserModel.principal_id == principal_id)
            .with_for_update()
        )
        if user_model is None:
            raise RuntimeError("UserModel disappeared during personalization write")
        return user_model
