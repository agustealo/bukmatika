from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from bukmatika.personalization.domain import ContextManifest


class CapabilityRisk(StrEnum):
    READ_ONLY = "read_only"
    NETWORK_READ = "network_read"
    CONSEQUENTIAL = "consequential"


class ActionDecisionValue(StrEnum):
    ALLOW = "allow"
    ALLOW_WITH_NOTIFICATION = "allow_with_notification"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PlannerStep(BaseModel):
    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    capability: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=1000)


class PlannerProposal(BaseModel):
    summary: str = Field(min_length=1, max_length=2000)
    steps: list[PlannerStep] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_step_ids(self) -> "PlannerProposal":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Planner step IDs must be unique")
        return self


class ValidatedPlannerStep(BaseModel):
    step_id: str
    capability: str
    arguments: dict[str, Any]
    rationale: str
    risk: CapabilityRisk


class ValidatedPlan(BaseModel):
    summary: str
    steps: list[ValidatedPlannerStep]


class StepPolicyDecision(BaseModel):
    step_id: str
    capability: str
    decision: ActionDecisionValue
    reason: str
    policy_version: str


class PlanEvaluation(BaseModel):
    plan_id: UUID
    status: str
    summary: str
    decisions: list[StepPolicyDecision]


class ModelTask(StrEnum):
    PLAN = "plan"


class ModelRequest(BaseModel):
    task: ModelTask
    user_request: str = Field(min_length=1, max_length=8000)
    context: ContextManifest


class ModelResponse(BaseModel):
    provider: str
    model: str
    output: dict[str, Any]

    @field_validator("provider", "model")
    @classmethod
    def nonempty_identity(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Model provider and model identifiers cannot be empty")
        return normalized
