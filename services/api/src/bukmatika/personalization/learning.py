from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.learning import (
    FormatEvidence,
    LearningRepository,
    OutcomeReferenceDenied,
)
from bukmatika.personalization.domain import PreferenceKey, PreferenceScopeType

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

FORMAT_MIN_DISTINCT_DOCUMENTS = 4
FORMAT_MIN_MARGIN = 2
FORMAT_EVIDENCE_WINDOW_DAYS = 180
FORMAT_DECAY_HALF_LIFE_DAYS = 90.0
INFERRED_EXPIRY_CONFIDENCE = 0.35


class OutcomeValue(StrEnum):
    HELPED = "helped"
    ACCEPTED = "accepted"
    IGNORED = "ignored"
    REJECTED = "rejected"
    UNDONE = "undone"
    CORRECTED = "corrected"
    FAILED = "failed"


class OutcomeCreate(BaseModel):
    outcome: OutcomeValue
    plan_id: UUID | None = None
    action_decision_id: UUID | None = None
    entity_type: str | None = Field(default=None, max_length=32)
    entity_id: UUID | None = None
    context: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_entity_reference(self) -> "OutcomeCreate":
        if (self.entity_type is None) != (self.entity_id is None):
            raise ValueError("entity_type and entity_id must be provided together")
        return self


class LearningService:
    """Deterministic, evidence-backed personalization learning authority."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def record_outcome(
        self,
        *,
        principal_id: UUID,
        request: OutcomeCreate,
        now: datetime | None = None,
    ) -> UUID:
        observed_at = now or datetime.now(UTC)
        async with self._session_scope() as database_session:
            repository = LearningRepository(database_session)
            event = await repository.record_outcome(
                principal_id=principal_id,
                outcome=request.outcome.value,
                plan_id=request.plan_id,
                action_decision_id=request.action_decision_id,
                entity_type=request.entity_type,
                entity_id=request.entity_id,
                context=dict(request.context),
            )
            user_model = await repository.lock_user_model(principal_id)
            if not user_model.learning_enabled:
                return event.id
            if request.entity_type != "preference_claim" or request.entity_id is None:
                return event.id

            claim = await repository.inferred_claim_for_update(
                principal_id=principal_id,
                claim_id=request.entity_id,
            )
            if claim is None:
                return event.id

            if request.outcome is OutcomeValue.CORRECTED:
                claim.confidence = max(0.0, claim.confidence - 0.35)
                claim.status = "contradicted"
            elif request.outcome is OutcomeValue.UNDONE:
                claim.confidence = max(0.0, claim.confidence - 0.25)
            elif request.outcome is OutcomeValue.REJECTED:
                claim.confidence = max(0.0, claim.confidence - 0.20)
            elif request.outcome is OutcomeValue.IGNORED:
                claim.confidence = max(0.0, claim.confidence - 0.05)
            elif request.outcome is OutcomeValue.FAILED:
                claim.confidence = max(0.0, claim.confidence - 0.10)
            elif request.outcome in (OutcomeValue.ACCEPTED, OutcomeValue.HELPED):
                claim.confidence = min(0.95, claim.confidence + 0.05)
                claim.last_reinforced_at = observed_at

            if (
                claim.status == "active"
                and request.outcome
                in (
                    OutcomeValue.REJECTED,
                    OutcomeValue.UNDONE,
                    OutcomeValue.IGNORED,
                    OutcomeValue.FAILED,
                )
                and claim.confidence < INFERRED_EXPIRY_CONFIDENCE
            ):
                claim.status = "contradicted"
            claim.updated_at = observed_at
            await database_session.flush()
            return event.id

    async def refresh_format_preference(
        self,
        *,
        principal_id: UUID,
        now: datetime | None = None,
    ) -> UUID | None:
        observed_at = now or datetime.now(UTC)
        cutoff = observed_at - timedelta(days=FORMAT_EVIDENCE_WINDOW_DAYS)
        async with self._session_scope() as database_session:
            repository = LearningRepository(database_session)
            user_model = await repository.lock_user_model(principal_id)
            if not user_model.learning_enabled:
                return None

            active = await repository.active_claim(
                principal_id=principal_id,
                key=PreferenceKey.FORMAT_PREFERRED.value,
                scope_type=PreferenceScopeType.GLOBAL.value,
                scope_value="",
            )
            if active is not None and active.source == "explicit":
                return None

            evidence = await repository.format_open_evidence(
                principal_id=principal_id,
                since=cutoff,
            )
            latest_by_document: dict[UUID, FormatEvidence] = {}
            for item in evidence:
                latest_by_document[item.document_id] = item

            grouped: defaultdict[str, list[FormatEvidence]] = defaultdict(list)
            for item in latest_by_document.values():
                grouped[item.format.upper()].append(item)
            ranked = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0]))
            if not ranked:
                return active.id if active is not None else None

            winning_format, winning_evidence = ranked[0]
            support = len(winning_evidence)
            runner_up = len(ranked[1][1]) if len(ranked) > 1 else 0
            margin = support - runner_up
            if support < FORMAT_MIN_DISTINCT_DOCUMENTS or margin < FORMAT_MIN_MARGIN:
                return active.id if active is not None else None

            confidence = _format_confidence(support=support, margin=margin)
            if active is not None and active.source == "inferred":
                active_format = str(active.value.get("format", "")).upper()
                if active_format == winning_format:
                    reinforced = await repository.reinforce_inferred_claim(
                        claim=active,
                        confidence=confidence,
                        evidence=winning_evidence,
                        now=observed_at,
                    )
                    return reinforced.id
                active.status = "contradicted"
                active.updated_at = observed_at
                await database_session.flush()

            claim = await repository.create_inferred_claim(
                user_model=user_model,
                key=PreferenceKey.FORMAT_PREFERRED.value,
                value={"format": winning_format},
                scope_type=PreferenceScopeType.GLOBAL.value,
                scope_value="",
                confidence=confidence,
                evidence=winning_evidence,
                decay_half_life_days=FORMAT_DECAY_HALF_LIFE_DAYS,
                now=observed_at,
            )
            return claim.id

    async def apply_decay(
        self,
        *,
        principal_id: UUID,
        now: datetime | None = None,
    ) -> int:
        observed_at = now or datetime.now(UTC)
        async with self._session_scope() as database_session:
            repository = LearningRepository(database_session)
            user_model = await repository.lock_user_model(principal_id)
            if not user_model.learning_enabled:
                return 0

            changed = 0
            claims = await repository.active_inferred_claims(principal_id)
            for claim in claims:
                half_life = claim.decay_half_life_days
                if half_life is None:
                    continue
                anchor = max(claim.last_reinforced_at, claim.updated_at)
                elapsed_days = (observed_at - anchor).total_seconds() / 86_400
                if elapsed_days < 1:
                    continue
                confidence = claim.confidence * (0.5 ** (elapsed_days / half_life))
                claim.confidence = max(0.0, min(1.0, confidence))
                if claim.confidence < INFERRED_EXPIRY_CONFIDENCE:
                    claim.status = "expired"
                claim.updated_at = observed_at
                changed += 1
            if changed:
                await database_session.flush()
            return changed


def _format_confidence(*, support: int, margin: int) -> float:
    confidence = 0.55 + (0.04 * support) + (0.015 * min(margin, 4))
    return min(0.90, round(confidence, 4))


__all__ = [
    "FORMAT_DECAY_HALF_LIFE_DAYS",
    "FORMAT_EVIDENCE_WINDOW_DAYS",
    "FORMAT_MIN_DISTINCT_DOCUMENTS",
    "FORMAT_MIN_MARGIN",
    "INFERRED_EXPIRY_CONFIDENCE",
    "LearningService",
    "OutcomeCreate",
    "OutcomeReferenceDenied",
    "OutcomeValue",
]
