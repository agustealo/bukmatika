from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from bukmatika.personalization.domain import PreferenceClaimResponse


class PreferenceProvenanceSummary(BaseModel):
    event_count: int
    distinct_entity_count: int
    event_types: list[str]
    first_evidence_at: datetime | None
    last_evidence_at: datetime | None


class InspectablePreferenceResponse(PreferenceClaimResponse):
    provenance: PreferenceProvenanceSummary | None = None


class ActiveGoalResponse(BaseModel):
    goal_id: UUID
    title: str
    kind: str
    status: str
    created_at: datetime
    updated_at: datetime


class ActivityOutcomeResponse(BaseModel):
    outcome_id: UUID
    plan_id: UUID | None
    action_decision_id: UUID | None
    outcome: str
    entity_type: str | None
    entity_id: UUID | None
    occurred_at: datetime


class ActivityLedgerItem(BaseModel):
    decision_id: UUID
    plan_id: UUID
    plan_status: str
    user_request: str
    step_id: str
    capability: str
    rationale: str | None
    decision: str
    decision_reason: str
    policy_version: str
    approval_required: bool
    approval_status: str | None = None
    approval_decided_at: datetime | None = None
    execution_completed: bool = False
    executed_at: datetime | None = None
    plan_created_at: datetime
    evaluated_at: datetime
    outcomes: list[ActivityOutcomeResponse]
    latest_outcome: str | None
    model_provider: str | None = None
    model_name: str | None = None


class ActivityLedgerResponse(BaseModel):
    items: list[ActivityLedgerItem]


class PersonalizationControlCenterResponse(BaseModel):
    user_model_id: UUID
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int
    explicit_preferences: list[InspectablePreferenceResponse]
    inferred_preferences: list[InspectablePreferenceResponse]
    active_goals: list[ActiveGoalResponse]
    recent_activity: list[ActivityLedgerItem]
    recent_outcomes: list[ActivityOutcomeResponse]
