from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.action_models import ActionExecutionReceipt
from bukmatika.persistence.events import SemanticEventType
from bukmatika.persistence.models import InteractionEvent
from bukmatika.persistence.personalization_models import (
    ActionApproval,
    ActionDecision,
    Goal,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
    PreferenceClaimEvidence,
)


@dataclass(frozen=True, slots=True)
class PreferenceEvidenceRow:
    claim_id: UUID
    event_type: str
    entity_type: str | None
    entity_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ActivityRow:
    plan: Plan
    decision: ActionDecision


@dataclass(frozen=True, slots=True)
class ModelActivityRow:
    decision_id: UUID
    event_type: str
    provider: str
    model: str
    routing: str | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ApprovalActivityRow:
    decision_id: UUID
    approval_status: str
    approval_decided_at: datetime
    executed_at: datetime | None


class PersonalizationReadRepository:
    """Principal-scoped read projection over canonical personalization evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def preference_evidence(
        self,
        *,
        principal_id: UUID,
        claim_ids: list[UUID],
    ) -> list[PreferenceEvidenceRow]:
        if not claim_ids:
            return []
        rows = (
            await self._session.execute(
                select(
                    PreferenceClaimEvidence.preference_claim_id,
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
                    PreferenceClaimEvidence.preference_claim_id.in_(claim_ids),
                )
                .order_by(
                    PreferenceClaimEvidence.preference_claim_id,
                    InteractionEvent.occurred_at,
                    InteractionEvent.id,
                )
            )
        ).all()
        return [
            PreferenceEvidenceRow(
                claim_id=claim_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                occurred_at=occurred_at,
            )
            for claim_id, event_type, entity_type, entity_id, occurred_at in rows
        ]

    async def active_goals(
        self,
        *,
        principal_id: UUID,
        limit: int = 20,
    ) -> list[Goal]:
        values = await self._session.scalars(
            select(Goal)
            .where(
                Goal.principal_id == principal_id,
                Goal.status == "active",
            )
            .order_by(Goal.updated_at.desc(), Goal.id.desc())
            .limit(limit)
        )
        return list(values)

    async def recent_activity(
        self,
        *,
        principal_id: UUID,
        limit: int,
    ) -> list[ActivityRow]:
        rows = (
            await self._session.execute(
                select(Plan, ActionDecision)
                .join(ActionDecision, ActionDecision.plan_id == Plan.id)
                .where(
                    Plan.principal_id == principal_id,
                    ActionDecision.principal_id == principal_id,
                )
                .order_by(ActionDecision.evaluated_at.desc(), ActionDecision.id.desc())
                .limit(limit)
            )
        ).all()
        return [ActivityRow(plan=plan, decision=decision) for plan, decision in rows]

    async def approval_activity_for_decisions(
        self,
        *,
        principal_id: UUID,
        decision_ids: list[UUID],
    ) -> list[ApprovalActivityRow]:
        if not decision_ids:
            return []

        approval_rows = (
            await self._session.execute(
                select(
                    ActionApproval.action_decision_id,
                    ActionApproval.decision,
                    ActionApproval.decided_at,
                ).where(
                    ActionApproval.principal_id == principal_id,
                    ActionApproval.action_decision_id.in_(decision_ids),
                )
            )
        ).all()
        receipt_rows = (
            await self._session.execute(
                select(
                    ActionExecutionReceipt.action_decision_id,
                    ActionExecutionReceipt.executed_at,
                ).where(
                    ActionExecutionReceipt.principal_id == principal_id,
                    ActionExecutionReceipt.action_decision_id.in_(decision_ids),
                )
            )
        ).all()
        executed_at_by_decision = {
            decision_id: executed_at for decision_id, executed_at in receipt_rows
        }
        return [
            ApprovalActivityRow(
                decision_id=decision_id,
                approval_status=approval_status,
                approval_decided_at=decided_at,
                executed_at=executed_at_by_decision.get(decision_id),
            )
            for decision_id, approval_status, decided_at in approval_rows
        ]

    async def model_activity_for_decisions(
        self,
        *,
        principal_id: UUID,
        decision_ids: list[UUID],
    ) -> list[ModelActivityRow]:
        if not decision_ids:
            return []
        rows = (
            await self._session.execute(
                select(
                    InteractionEvent.entity_id,
                    InteractionEvent.event_type,
                    InteractionEvent.context,
                    InteractionEvent.occurred_at,
                )
                .where(
                    InteractionEvent.principal_id == principal_id,
                    InteractionEvent.entity_type == "action_decision",
                    InteractionEvent.entity_id.in_(decision_ids),
                    InteractionEvent.event_type.in_(
                        (
                            SemanticEventType.AI_MODEL_COMPLETED.value,
                            SemanticEventType.AI_MODEL_FAILED.value,
                        )
                    ),
                )
                .order_by(InteractionEvent.occurred_at, InteractionEvent.id)
            )
        ).all()
        values: list[ModelActivityRow] = []
        for decision_id, event_type, context, occurred_at in rows:
            if decision_id is None:
                continue
            provider = context.get("provider")
            model = context.get("model")
            routing = context.get("routing")
            if not isinstance(provider, str) or not isinstance(model, str):
                continue
            values.append(
                ModelActivityRow(
                    decision_id=decision_id,
                    event_type=event_type,
                    provider=provider,
                    model=model,
                    routing=routing if isinstance(routing, str) else None,
                    occurred_at=occurred_at,
                )
            )
        return values

    async def outcomes_for_decisions(
        self,
        *,
        principal_id: UUID,
        decision_ids: list[UUID],
    ) -> list[OutcomeEvent]:
        if not decision_ids:
            return []
        values = await self._session.scalars(
            select(OutcomeEvent)
            .where(
                OutcomeEvent.principal_id == principal_id,
                OutcomeEvent.action_decision_id.in_(decision_ids),
            )
            .order_by(OutcomeEvent.occurred_at, OutcomeEvent.id)
        )
        return list(values)

    async def recent_outcomes(
        self,
        *,
        principal_id: UUID,
        limit: int = 30,
    ) -> list[OutcomeEvent]:
        values = await self._session.scalars(
            select(OutcomeEvent)
            .where(OutcomeEvent.principal_id == principal_id)
            .order_by(OutcomeEvent.occurred_at.desc(), OutcomeEvent.id.desc())
            .limit(limit)
        )
        return list(values)
