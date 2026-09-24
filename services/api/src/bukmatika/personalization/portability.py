from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.delegation_control import revoke_level2_consent_in_session
from bukmatika.persistence import session_scope
from bukmatika.persistence.personalization_portability import (
    PersonalizationPortabilityRepository,
    PreferenceEvidenceRecord,
)
from bukmatika.personalization.portability_domain import (
    ActionDecisionExport,
    GoalExport,
    OutcomeEventExport,
    PersonalizationExportResponse,
    PersonalizationResetResponse,
    PlanExport,
    PreferenceClaimExport,
    PreferenceEvidenceExport,
    UserModelExport,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PersonalizationPortabilityService:
    """User-owned export and destructive reset boundary for personalization state."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def export(self, *, principal_id: UUID) -> PersonalizationExportResponse:
        async with self._session_scope() as database_session:
            records = await PersonalizationPortabilityRepository(database_session).export_records(
                principal_id
            )
            evidence_by_claim: dict[UUID, list[PreferenceEvidenceRecord]] = defaultdict(list)
            for evidence in records.evidence:
                evidence_by_claim[evidence.claim_id].append(evidence)

            return PersonalizationExportResponse(
                exported_at=datetime.now(UTC),
                latest_reset_at=records.latest_reset_at,
                user_model=UserModelExport(
                    user_model_id=records.user_model.id,
                    ai_enabled=records.user_model.ai_enabled,
                    learning_enabled=records.user_model.learning_enabled,
                    autonomy_level=records.user_model.autonomy_level,
                    model_provider_override=records.user_model.model_provider_override,
                    model_name_override=records.user_model.model_name_override,
                    created_at=records.user_model.created_at,
                    updated_at=records.user_model.updated_at,
                ),
                preferences=[
                    PreferenceClaimExport(
                        claim_id=claim.id,
                        key=claim.key,
                        value=claim.value,
                        source=claim.source,
                        status=claim.status,
                        confidence=claim.confidence,
                        scope_type=claim.scope_type,
                        scope_value=claim.scope_value,
                        evidence_count=claim.evidence_count,
                        first_observed_at=claim.first_observed_at,
                        last_reinforced_at=claim.last_reinforced_at,
                        decay_half_life_days=claim.decay_half_life_days,
                        influence=claim.influence,
                        deleted_at=claim.deleted_at,
                        created_at=claim.created_at,
                        updated_at=claim.updated_at,
                        evidence=[
                            PreferenceEvidenceExport(
                                interaction_event_id=evidence.interaction_event_id,
                                event_type=evidence.event_type,
                                entity_type=evidence.entity_type,
                                entity_id=evidence.entity_id,
                                occurred_at=evidence.occurred_at,
                            )
                            for evidence in evidence_by_claim.get(claim.id, [])
                        ],
                    )
                    for claim in records.preferences
                ],
                goals=[
                    GoalExport(
                        goal_id=goal.id,
                        title=goal.title,
                        kind=goal.kind,
                        status=goal.status,
                        scope=goal.scope,
                        constraints=goal.constraints,
                        created_at=goal.created_at,
                        updated_at=goal.updated_at,
                    )
                    for goal in records.goals
                ],
                plans=[
                    PlanExport(
                        plan_id=plan.id,
                        goal_id=plan.goal_id,
                        status=plan.status,
                        user_request=plan.user_request,
                        planner_version=plan.planner_version,
                        steps=plan.steps,
                        context_manifest=plan.context_manifest,
                        created_at=plan.created_at,
                        updated_at=plan.updated_at,
                    )
                    for plan in records.plans
                ],
                action_decisions=[
                    ActionDecisionExport(
                        decision_id=decision.id,
                        plan_id=decision.plan_id,
                        step_id=decision.step_id,
                        capability=decision.capability,
                        decision=decision.decision,
                        reason=decision.reason,
                        policy_version=decision.policy_version,
                        evaluated_at=decision.evaluated_at,
                    )
                    for decision in records.decisions
                ],
                outcomes=[
                    OutcomeEventExport(
                        outcome_id=outcome.id,
                        plan_id=outcome.plan_id,
                        action_decision_id=outcome.action_decision_id,
                        outcome=outcome.outcome,
                        entity_type=outcome.entity_type,
                        entity_id=outcome.entity_id,
                        context=outcome.context,
                        occurred_at=outcome.occurred_at,
                    )
                    for outcome in records.outcomes
                ],
            )

    async def reset(self, *, principal_id: UUID) -> PersonalizationResetResponse:
        async with self._session_scope() as database_session:
            await revoke_level2_consent_in_session(
                database_session,
                principal_id=principal_id,
                reason="personalization_reset",
            )
            repository = PersonalizationPortabilityRepository(database_session)
            result = await repository.reset(principal_id)
            return PersonalizationResetResponse(
                reset_at=result.reset_at,
                user_model_id=result.user_model.id,
                ai_enabled=result.user_model.ai_enabled,
                learning_enabled=result.user_model.learning_enabled,
                autonomy_level=result.user_model.autonomy_level,
                model_provider_override=result.user_model.model_provider_override,
                model_name_override=result.user_model.model_name_override,
            )