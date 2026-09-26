from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


RecommendationSignal = Literal[
    "subject",
    "author",
    "region",
    "period",
    "language",
    "format",
    "source",
]


class RecommendationReason(BaseModel):
    claim_id: UUID
    preference_key: str
    source: Literal["explicit", "inferred"]
    confidence: float = Field(ge=0, le=1)
    signal: RecommendationSignal
    matched_values: list[str]
    contribution: float = Field(gt=0)


class PersonalizedRecommendation(BaseModel):
    work_id: UUID
    title: str
    authors: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    formats: list[str] = Field(default_factory=list)
    publication_years: list[int] = Field(default_factory=list)
    source: str
    source_record_id: str
    fit_score: float = Field(gt=0)
    reasons: list[RecommendationReason]


class PersonalizedRecommendationsResponse(BaseModel):
    items: list[PersonalizedRecommendation]
    active_ranking_claims: int = Field(ge=0)
    supported_ranking_claims: int = Field(ge=0)
    explanation: str


__all__ = [
    "PersonalizedRecommendation",
    "PersonalizedRecommendationsResponse",
    "RecommendationReason",
    "RecommendationSignal",
]
