from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from bukmatika.research import GroundedResearchAnswer, ResearchEvidenceBundleResponse


class GroundedResearchSelectionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    library_entry_ids: list[UUID] = Field(min_length=1, max_length=20)
    related_limit: int = Field(default=12, ge=0, le=30)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Research question cannot be blank")
        return normalized

    @field_validator("library_entry_ids")
    @classmethod
    def require_unique_entries(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Selected library entries must be unique")
        return value


class GroundedResearchCapabilityOutput(BaseModel):
    evidence: ResearchEvidenceBundleResponse
    answer: GroundedResearchAnswer
    model_provider: str
    model_name: str
    model_routing: str


class GroundedResearchExecutionResponse(BaseModel):
    plan_id: UUID
    action_decision_id: UUID
    evidence: ResearchEvidenceBundleResponse
    answer: GroundedResearchAnswer
    model_provider: str
    model_name: str
    model_routing: str
