from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.routing_policy_models import AIRoutingPolicy


class AIRoutingPolicyRepository:
    """Canonical principal-owned model-selection and cloud-egress policy persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, *, principal_id: UUID) -> AIRoutingPolicy:
        user_model = await PersonalizationRepository(self._session).get_or_create_user_model(
            principal_id
        )
        selection_mode = (
            "profile"
            if user_model.model_provider_override not in (None, "none")
            else "installation_default"
        )
        statement = (
            insert(AIRoutingPolicy)
            .values(
                principal_id=principal_id,
                user_model_id=user_model.id,
                model_selection_mode=selection_mode,
                cloud_egress_policy="local_only",
            )
            .on_conflict_do_nothing(constraint="uq_ai_routing_policy_principal")
            .returning(AIRoutingPolicy)
        )
        created = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self._session.scalar(
            select(AIRoutingPolicy).where(AIRoutingPolicy.principal_id == principal_id)
        )
        if existing is None:
            raise RuntimeError("AI routing policy upsert returned no row")
        return existing
