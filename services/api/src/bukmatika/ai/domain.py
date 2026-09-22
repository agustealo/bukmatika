import json
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator, model_validator

from bukmatika.personalization.domain import ContextManifest

MAX_PLAN_STEPS = 12
MAX_STEP_ARGUMENT_BYTES = 8_192


class CapabilityName(StrEnum):
    DISCOVERY_SEARCH = "discovery.search"
    CATALOG_SEARCH = "catalog.search"
    LIBRARY_LIST = "library.list"
    LIBRARY_SAVE = "library.save"
    READER_OPEN = "reader.open"
    RESEARCH_SEARCH = "research.search"
    RESEARCH_ANSWER = "research.answer"
    ACQUISITION_REQUEST = "acquisition.request"
    PREFERENCES_PROPOSE = "preferences.propose"
    GOALS_UPDATE = "goals.update"


class PlanStep(BaseModel):
    step_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    capability: CapabilityName
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    rationale: str = Field(min_length=1, max_length=500)

    @field_validator("arguments")
    @classmethod
    def bound_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_STEP_ARGUMENT_BYTES:
            raise ValueError("Plan step arguments exceed the allowed byte budget")
        return value


class PlanProposal(BaseModel):
    summary: str = Field(min_length=1, max_length=1_000)
    steps: list[PlanStep] = Field(min_length=1, max_length=MAX_PLAN_STEPS)

    @model_validator(mode="after")
    def unique_step_ids(self) -> "PlanProposal":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Plan step IDs must be unique")
        return self


class PlannedActionDecision(BaseModel):
    step_id: str
    capability: CapabilityName
    decision: str
    reason: str
    policy_version: str


class PersistedPlanResponse(BaseModel):
    plan_id: UUID
    status: str
    summary: str
    context: ContextManifest
    steps: list[PlanStep]
    decisions: list[PlannedActionDecision]
