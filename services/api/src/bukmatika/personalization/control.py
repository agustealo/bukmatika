from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import (
    ActionDecision,
    Goal,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
)
from bukmatika.persistence.personalization_read import (
    ActivityRow,
    PersonalizationReadRepository,
    PreferenceEvidenceRow,
)
from bukmatika.personalization.control_domain import (
    ActiveGoalResponse,
    ActivityLedgerItem,
    ActivityLedgerResponse,
    ActivityOutcomeResponse,
    InspectablePreferenceResponse,
    PersonalizationControlCenterResponse,
    PreferenceProvenanceSummary,
)
from bukmatika.personalization.domain import (
    PreferenceInfluence,
    PreferenceKey,
    PreferenceScopeType,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PersonalizationControlService:
    """Builds safe user-facing personalization and AI activity projections."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def snapshot(
        self,
        *,
        principal_id: UUID,
        activity_limit: int = 12,
        outcome_limit: int = 30,
    ) -> PersonalizationControlCenterResponse:
        async with self._session_scope() as database_session:
            personalization = PersonalizationRepository(database_session)
            reads = PersonalizationReadRepository(database_session)

            user_model = await personalization.get_or_create_user_model(principal_id)
            claims = await personalization.active_claims(principal_id)
            inferred_ids = [claim.id for claim in claims if claim.source == "inferred"]
            evidence = await reads.preference_evidence(
                principal_id=principal_id,
                claim_ids=inferred_ids,
            )
            evidence_by_claim = _evidence_by_claim(evidence)

            explicit = [
                _claim_response(claim, evidence_by_claim.get(claim.id))
                for claim in claims
                if claim.source == "explicit"
            ]
            inferred = [
                _claim_response(claim, evidence_by_claim.get(claim.id))
                for claim in claims
                if claim.source == "inferred"
            ]

            goals = await reads.active_goals(principal_id=principal_id)
            activity_rows = await reads.recent_activity(
                principal_id=principal_id,
                limit=activity_limit,
            )
            activity_outcomes = await reads.outcomes_for_decisions(
                principal_id=principal_id,
                decision_ids=[row.decision.id for row in activity_rows],
            )
            recent_outcomes = await reads.recent_outcomes(
                principal_id=principal_id,
                limit=outcome_limit,
            )

            return PersonalizationControlCenterResponse(
                user_model_id=user_model.id,
                ai_enabled=user_model.ai_enabled,
                learning_enabled=user_model.learning_enabled,
                autonomy_level=user_model.autonomy_level,
                explicit_preferences=explicit,
                inferred_preferences=inferred,
                active_goals=[_goal_response(goal) for goal in goals],
                recent_activity=_activity_items(activity_rows, activity_outcomes),
                recent_outcomes=[_outcome_response(outcome) for outcome in recent_outcomes],
            )

    async def activity(
        self,
        *,
        principal_id: UUID,
        limit: int = 25,
    ) -> ActivityLedgerResponse:
        async with self._session_scope() as database_session:
            reads = PersonalizationReadRepository(database_session)
            rows = await reads.recent_activity(principal_id=principal_id, limit=limit)
            outcomes = await reads.outcomes_for_decisions(
                principal_id=principal_id,
                decision_ids=[row.decision.id for row in rows],
            )
            return ActivityLedgerResponse(items=_activity_items(rows, outcomes))


def _evidence_by_claim(
    rows: list[PreferenceEvidenceRow],
) -> dict[UUID, list[PreferenceEvidenceRow]]:
    grouped: dict[UUID, list[PreferenceEvidenceRow]] = defaultdict(list)
    for row in rows:
        grouped[row.claim_id].append(row)
    return grouped


def _claim_response(
    claim: PreferenceClaim,
    evidence: list[PreferenceEvidenceRow] | None,
) -> InspectablePreferenceResponse:
    provenance: PreferenceProvenanceSummary | None = None
    if claim.source == "inferred":
        rows = evidence or []
        entity_ids = {row.entity_id for row in rows if row.entity_id is not None}
        provenance = PreferenceProvenanceSummary(
            event_count=len(rows),
            distinct_entity_count=len(entity_ids),
            event_types=sorted({row.event_type for row in rows}),
            first_evidence_at=min((row.occurred_at for row in rows), default=None),
            last_evidence_at=max((row.occurred_at for row in rows), default=None),
        )

    return InspectablePreferenceResponse(
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
        provenance=provenance,
    )


def _goal_response(goal: Goal) -> ActiveGoalResponse:
    return ActiveGoalResponse(
        goal_id=goal.id,
        title=goal.title,
        kind=goal.kind,
        status=goal.status,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _outcome_response(outcome: OutcomeEvent) -> ActivityOutcomeResponse:
    return ActivityOutcomeResponse(
        outcome_id=outcome.id,
        plan_id=outcome.plan_id,
        action_decision_id=outcome.action_decision_id,
        outcome=outcome.outcome,
        entity_type=outcome.entity_type,
        entity_id=outcome.entity_id,
        occurred_at=outcome.occurred_at,
    )


def _activity_items(
    rows: list[ActivityRow],
    outcomes: list[OutcomeEvent],
) -> list[ActivityLedgerItem]:
    outcomes_by_decision: dict[UUID, list[OutcomeEvent]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.action_decision_id is not None:
            outcomes_by_decision[outcome.action_decision_id].append(outcome)

    return [
        _activity_item(
            plan=row.plan,
            decision=row.decision,
            outcomes=outcomes_by_decision.get(row.decision.id, []),
        )
        for row in rows
    ]


def _activity_item(
    *,
    plan: Plan,
    decision: ActionDecision,
    outcomes: list[OutcomeEvent],
) -> ActivityLedgerItem:
    rendered_outcomes = [_outcome_response(outcome) for outcome in outcomes]
    return ActivityLedgerItem(
        decision_id=decision.id,
        plan_id=plan.id,
        plan_status=plan.status,
        user_request=plan.user_request,
        step_id=decision.step_id,
        capability=decision.capability,
        rationale=_step_rationale(plan.steps, decision.step_id),
        decision=decision.decision,
        decision_reason=decision.reason,
        policy_version=decision.policy_version,
        approval_required=decision.decision == "require_approval",
        plan_created_at=plan.created_at,
        evaluated_at=decision.evaluated_at,
        outcomes=rendered_outcomes,
        latest_outcome=rendered_outcomes[-1].outcome if rendered_outcomes else None,
        model_provider=None,
        model_name=None,
    )


def _step_rationale(steps: list[dict[str, object]], step_id: str) -> str | None:
    for step in steps:
        if step.get("step_id") != step_id:
            continue
        rationale = step.get("rationale")
        return rationale if isinstance(rationale, str) else None
    return None
