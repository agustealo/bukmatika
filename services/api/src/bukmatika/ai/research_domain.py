from uuid import UUID

from pydantic import BaseModel

from bukmatika.research import GroundedResearchAnswer, ResearchEvidenceBundleResponse


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
