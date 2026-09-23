from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue


class UserModelExport(BaseModel):
    user_model_id: UUID
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int
    model_provider_override: str | None
    model_name_override: str | None
    created_at: datetime
    updated_at: datetime


class PreferenceEvidenceExport(BaseModel):
    interaction_event_id: UUID
    event_type: str
    entity_type: str | None
    entity_id: UUID | None
    occurred_at: datetime


class PreferenceClaimExport(BaseModel):
    claim_id: UUID
    key: str
    value: dict[str, JsonValue]
    source: str
    status: str
    confidence: float
    scope_type: str
    scope_value: str
    evidence_count: int
    first_observed_at: datetime
    last_reinforced_at: datetime
    decay_half_life_days: float | None
    influence: dict[str, bool]
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    evidence: list[PreferenceEvidenceExport]


class GoalExport(BaseModel):
    goal_id: UUID
    title: str
    kind: str
    status: str
    scope: dict[str, JsonValue]
    constraints: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime


class PlanExport(BaseModel):
    plan_id: UUID
    goal_id: UUID | None
    status: str
    user_request: str
    planner_version: str
    steps: list[dict[str, JsonValue]]
    context_manifest: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime


class ActionDecisionExport(BaseModel):
    decision_id: UUID
    plan_id: UUID
    step_id: str
    capability: str
    decision: str
    reason: str
    policy_version: str
    evaluated_at: datetime


class OutcomeEventExport(BaseModel):
    outcome_id: UUID
    plan_id: UUID | None
    action_decision_id: UUID | None
    outcome: str
    entity_type: str | None
    entity_id: UUID | None
    context: dict[str, JsonValue]
    occurred_at: datetime


class PersonalizationExportResponse(BaseModel):
    schema_version: Literal[1] = 1
    exported_at: datetime
    latest_reset_at: datetime | None
    user_model: UserModelExport
    preferences: list[PreferenceClaimExport]
    goals: list[GoalExport]
    plans: list[PlanExport]
    action_decisions: list[ActionDecisionExport]
    outcomes: list[OutcomeEventExport]


class PersonalizationResetRequest(BaseModel):
    confirmation: Literal["RESET"] = Field(
        description="Explicit destructive confirmation required for personalization reset."
    )


class PersonalizationResetResponse(BaseModel):
    reset_at: datetime
    user_model_id: UUID
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int
    model_provider_override: str | None
    model_name_override: str | None
