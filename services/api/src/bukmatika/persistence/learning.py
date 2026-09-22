from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.events import SemanticEventType
from bukmatika.persistence.models import InteractionEvent
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import (
    ActionDecision,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
    PreferenceClaimEvidence,
    UserModel,
)


class OutcomeReferenceDenied(LookupError):
    code = "OUTCOME_REFERENCE_DENIED"


@dataclass(frozen=True, slots=True)
class FormatEvidence:
    event_id: UUID
    document_id: UUID
    format: str
    occurred_at: datetime


class LearningRepository:
    """Persistence authority for outcomes and inspectable learned preference evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_user_model(self, principal_id: UUID) -> UserModel:
        await PersonalizationRepository(self._session).get_or_create_user_model(principal_id)
        user_model = await self._session.scalar(
            select(UserModel)
            .where(UserModel.principal_id == principal_id)
            .with_for_update()
        )
        if user_model is None:
            raise RuntimeError("UserModel disappeared during learning mutation")
        return user_model

    async def record_outcome(
        self,
        *,
        principal_id: UUID,
        outcome: str,
        plan_id: UUID | None,
        action_decision_id: UUID | None,
        entity_type: str | None,
        entity_id: UUID | None,
        context: Mapping[str, object],
    ) -> OutcomeEvent:
        plan: Plan | None = None
        if plan_id is not None:
            plan = await self._session.scalar(
                select(Plan).where(
                    Plan.id == plan_id,
                    Plan.principal_id == principal_id,
                )
            )
            if plan is None:
                raise OutcomeReferenceDenied("Referenced plan is unavailable")

        decision: ActionDecision | None = None
        if action_decision_id is not None:
            decision = await self._session.scalar(
                select(ActionDecision).where(
                    ActionDecision.id == action_decision_id,
                    ActionDecision.principal_id == principal_id,
                )
            )
            if decision is None:
                raise OutcomeReferenceDenied("Referenced action decision is unavailable")
            if plan is not None and decision.plan_id != plan.id:
                raise OutcomeReferenceDenied("Action decision does not belong to referenced plan")
            if plan is None:
                plan = await self._session.scalar(
                    select(Plan).where(
                        Plan.id == decision.plan_id,
                        Plan.principal_id == principal_id,
                    )
                )
                if plan is None:
                    raise OutcomeReferenceDenied("Action decision plan is unavailable")

        if entity_type == "preference_claim" and entity_id is not None:
            claim = await self._session.scalar(
                select(PreferenceClaim.id).where(
                    PreferenceClaim.id == entity_id,
                    PreferenceClaim.principal_id == principal_id,
                )
            )
            if claim is None:
                raise OutcomeReferenceDenied("Referenced preference claim is unavailable")

        event = OutcomeEvent(
            principal_id=principal_id,
            plan_id=plan.id if plan is not None else None,
            action_decision_id=decision.id if decision is not None else None,
            outcome=outcome,
            entity_type=entity_type,
            entity_id=entity_id,
            context=dict(context),
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def latest_personalization_reset_at(self, principal_id: UUID) -> datetime | None:
        return await self._session.scalar(
            select(InteractionEvent.occurred_at)
            .where(
                InteractionEvent.principal_id == principal_id,
                InteractionEvent.event_type == SemanticEventType.PERSONALIZATION_RESET.value,
            )
            .order_by(InteractionEvent.occurred_at.desc(), InteractionEvent.id.desc())
            .limit(1)
        )

    async def format_open_evidence(
        self,
        *,
        principal_id: UUID,
        since: datetime,
    ) -> list[FormatEvidence]:
        rows = (
            await self._session.execute(
                select(
                    InteractionEvent.id,
                    InteractionEvent.entity_id,
                    Document.format,
                    InteractionEvent.occurred_at,
                )
                .join(Document, Document.id == InteractionEvent.entity_id)
                .where(
                    InteractionEvent.principal_id == principal_id,
                    InteractionEvent.event_type == "reader.opened",
                    InteractionEvent.entity_type == "document",
                    InteractionEvent.occurred_at > since,
                )
                .order_by(InteractionEvent.occurred_at, InteractionEvent.id)
            )
        ).all()
        return [
            FormatEvidence(
                event_id=event_id,
                document_id=document_id,
                format=format_name,
                occurred_at=occurred_at,
            )
            for event_id, document_id, format_name, occurred_at in rows
            if document_id is not None
        ]

    async def active_claim(
        self,
        *,
        principal_id: UUID,
        key: str,
        scope_type: str,
        scope_value: str,
    ) -> PreferenceClaim | None:
        return await self._session.scalar(
            select(PreferenceClaim)
            .where(
                PreferenceClaim.principal_id == principal_id,
                PreferenceClaim.key == key,
                PreferenceClaim.scope_type == scope_type,
                PreferenceClaim.scope_value == scope_value,
                PreferenceClaim.status == "active",
            )
            .with_for_update()
        )

    async def latest_inferred_contradiction(
        self,
        *,
        principal_id: UUID,
        key: str,
        scope_type: str,
        scope_value: str,
    ) -> PreferenceClaim | None:
        return await self._session.scalar(
            select(PreferenceClaim)
            .where(
                PreferenceClaim.principal_id == principal_id,
                PreferenceClaim.key == key,
                PreferenceClaim.scope_type == scope_type,
                PreferenceClaim.scope_value == scope_value,
                PreferenceClaim.source == "inferred",
                PreferenceClaim.status == "contradicted",
            )
            .order_by(PreferenceClaim.updated_at.desc(), PreferenceClaim.id.desc())
            .limit(1)
        )

    async def create_inferred_claim(
        self,
        *,
        user_model: UserModel,
        key: str,
        value: Mapping[str, object],
        scope_type: str,
        scope_value: str,
        confidence: float,
        evidence: list[FormatEvidence],
        decay_half_life_days: float,
        now: datetime,
    ) -> PreferenceClaim:
        if not evidence:
            raise ValueError("Inferred claims require evidence")
        claim = PreferenceClaim(
            user_model_id=user_model.id,
            principal_id=user_model.principal_id,
            key=key,
            value=dict(value),
            source="inferred",
            status="active",
            confidence=confidence,
            scope_type=scope_type,
            scope_value=scope_value,
            evidence_count=len(evidence),
            first_observed_at=min(item.occurred_at for item in evidence),
            last_reinforced_at=max(item.occurred_at for item in evidence),
            decay_half_life_days=decay_half_life_days,
            influence={"ranking": True, "presentation": True, "automation": False},
            updated_at=now,
        )
        self._session.add(claim)
        await self._session.flush()
        self._session.add_all(
            [
                PreferenceClaimEvidence(
                    preference_claim_id=claim.id,
                    interaction_event_id=item.event_id,
                )
                for item in evidence
            ]
        )
        await self._session.flush()
        return claim

    async def reinforce_inferred_claim(
        self,
        *,
        claim: PreferenceClaim,
        confidence: float,
        evidence: list[FormatEvidence],
        now: datetime,
    ) -> PreferenceClaim:
        if claim.source != "inferred":
            raise ValueError("Only inferred claims may be reinforced by the learning engine")
        existing_document_ids = {
            document_id
            for document_id in (
                await self._session.scalars(
                    select(InteractionEvent.entity_id)
                    .join(
                        PreferenceClaimEvidence,
                        PreferenceClaimEvidence.interaction_event_id == InteractionEvent.id,
                    )
                    .where(
                        PreferenceClaimEvidence.preference_claim_id == claim.id,
                        InteractionEvent.entity_type == "document",
                    )
                )
            ).all()
            if document_id is not None
        }
        new_evidence = [
            item for item in evidence if item.document_id not in existing_document_ids
        ]
        if not new_evidence:
            return claim

        self._session.add_all(
            [
                PreferenceClaimEvidence(
                    preference_claim_id=claim.id,
                    interaction_event_id=item.event_id,
                )
                for item in new_evidence
            ]
        )
        claim.confidence = confidence
        claim.evidence_count = len(existing_document_ids) + len(new_evidence)
        claim.last_reinforced_at = max(item.occurred_at for item in new_evidence)
        claim.updated_at = now
        await self._session.flush()
        return claim

    async def inferred_claim_for_update(
        self,
        *,
        principal_id: UUID,
        claim_id: UUID,
    ) -> PreferenceClaim | None:
        return await self._session.scalar(
            select(PreferenceClaim)
            .where(
                PreferenceClaim.id == claim_id,
                PreferenceClaim.principal_id == principal_id,
                PreferenceClaim.source == "inferred",
                PreferenceClaim.status == "active",
            )
            .with_for_update()
        )

    async def active_inferred_claims(self, principal_id: UUID) -> list[PreferenceClaim]:
        values = await self._session.scalars(
            select(PreferenceClaim)
            .where(
                PreferenceClaim.principal_id == principal_id,
                PreferenceClaim.source == "inferred",
                PreferenceClaim.status == "active",
            )
            .with_for_update()
        )
        return list(values)
