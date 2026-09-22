from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import InteractionEvent
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import (
    ActionDecision,
    Goal,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
    PreferenceClaimEvidence,
    UserModel,
)


@dataclass(frozen=True, slots=True)
class PreferenceEvidenceRecord:
    claim_id: UUID
    interaction_event_id: UUID
    event_type: str
    entity_type: str | None
    entity_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalizationExportRecords:
    user_model: UserModel
    preferences: list[PreferenceClaim]
    evidence: list[PreferenceEvidenceRecord]
    goals: list[Goal]
    plans: list[Plan]
    decisions: list[ActionDecision]
    outcomes: list[OutcomeEvent]
    latest_reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class PersonalizationResetResult:
    reset_at: datetime
    user_model: UserModel


class PersonalizationPortabilityRepository:
    """Finite principal-owned export and destructive personalization reset authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def export_records(self, principal_id: UUID) -> PersonalizationExportRecords:
        user_model = await PersonalizationRepository(self._session).get_or_create_user_model(
            principal_id
        )
        preferences = list(
            (
                await self._session.scalars(
                    select(PreferenceClaim)
                    .where(PreferenceClaim.principal_id == principal_id)
                    .order_by(PreferenceClaim.created_at, PreferenceClaim.id)
                )
            ).all()
        )
        evidence_rows = (
            await self._session.execute(
                select(
                    PreferenceClaimEvidence.preference_claim_id,
                    InteractionEvent.id,
                    InteractionEvent.event_type,
                    InteractionEvent.entity_type,
                    InteractionEvent.entity_id,
                    InteractionEvent.occurred_at,
                )
                .join(
                    PreferenceClaim,
                    PreferenceClaim.id == PreferenceClaimEvidence.preference_claim_id,
                )
                .join(
                    InteractionEvent,
                    InteractionEvent.id == PreferenceClaimEvidence.interaction_event_id,
                )
                .where(
                    PreferenceClaim.principal_id == principal_id,
                    InteractionEvent.principal_id == principal_id,
                )
                .order_by(
                    PreferenceClaimEvidence.preference_claim_id,
                    InteractionEvent.occurred_at,
                    InteractionEvent.id,
                )
            )
        ).all()
        evidence = [
            PreferenceEvidenceRecord(
                claim_id=claim_id,
                interaction_event_id=interaction_event_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                occurred_at=occurred_at,
            )
            for (
                claim_id,
                interaction_event_id,
                event_type,
                entity_type,
                entity_id,
                occurred_at,
            ) in evidence_rows
        ]
        goals = list(
            (
                await self._session.scalars(
                    select(Goal)
                    .where(Goal.principal_id == principal_id)
                    .order_by(Goal.created_at, Goal.id)
                )
            ).all()
        )
        plans = list(
            (
                await self._session.scalars(
                    select(Plan)
                    .where(Plan.principal_id == principal_id)
                    .order_by(Plan.created_at, Plan.id)
                )
            ).all()
        )
        decisions = list(
            (
                await self._session.scalars(
                    select(ActionDecision)
                    .where(ActionDecision.principal_id == principal_id)
                    .order_by(ActionDecision.evaluated_at, ActionDecision.id)
                )
            ).all()
        )
        outcomes = list(
            (
                await self._session.scalars(
                    select(OutcomeEvent)
                    .where(OutcomeEvent.principal_id == principal_id)
                    .order_by(OutcomeEvent.occurred_at, OutcomeEvent.id)
                )
            ).all()
        )
        latest_reset_at = await self.latest_reset_at(principal_id)
        return PersonalizationExportRecords(
            user_model=user_model,
            preferences=preferences,
            evidence=evidence,
            goals=goals,
            plans=plans,
            decisions=decisions,
            outcomes=outcomes,
            latest_reset_at=latest_reset_at,
        )

    async def latest_reset_at(self, principal_id: UUID) -> datetime | None:
        return await self._session.scalar(
            select(InteractionEvent.occurred_at)
            .where(
                InteractionEvent.principal_id == principal_id,
                InteractionEvent.event_type == SemanticEventType.PERSONALIZATION_RESET.value,
            )
            .order_by(InteractionEvent.occurred_at.desc(), InteractionEvent.id.desc())
            .limit(1)
        )

    async def reset(self, principal_id: UUID) -> PersonalizationResetResult:
        personalization = PersonalizationRepository(self._session)
        await personalization.get_or_create_user_model(principal_id)
        current_model = await self._session.scalar(
            select(UserModel)
            .where(UserModel.principal_id == principal_id)
            .with_for_update()
        )
        if current_model is None:
            raise RuntimeError("UserModel disappeared during personalization reset")

        await self._session.execute(
            delete(OutcomeEvent).where(OutcomeEvent.principal_id == principal_id)
        )
        await self._session.execute(
            delete(ActionDecision).where(ActionDecision.principal_id == principal_id)
        )
        await self._session.execute(delete(Plan).where(Plan.principal_id == principal_id))
        await self._session.execute(delete(Goal).where(Goal.principal_id == principal_id))
        await self._session.execute(
            delete(PreferenceClaim).where(PreferenceClaim.principal_id == principal_id)
        )
        await self._session.execute(delete(UserModel).where(UserModel.id == current_model.id))
        await self._session.flush()

        reset_event = await InteractionEventRepository(self._session).record(
            SemanticEventType.PERSONALIZATION_RESET,
            principal_id=principal_id,
            entity_type="user_model",
            context={"schema_version": 1},
        )
        fresh_model = UserModel(
            principal_id=principal_id,
            ai_enabled=True,
            learning_enabled=True,
            autonomy_level=0,
        )
        self._session.add(fresh_model)
        await self._session.flush()
        return PersonalizationResetResult(
            reset_at=reset_event.occurred_at,
            user_model=fresh_model,
        )
