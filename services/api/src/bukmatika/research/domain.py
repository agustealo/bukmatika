from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ResearchSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    library_entry_ids: list[UUID] = Field(min_length=1, max_length=20)
    limit: int = Field(default=30, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Research query cannot be blank")
        return normalized

    @field_validator("library_entry_ids")
    @classmethod
    def require_unique_entries(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Selected library entries must be unique")
        return value


class ResearchPassageResponse(BaseModel):
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    edition_id: UUID
    edition_title: str
    asset_id: UUID
    document_id: UUID
    chunk_id: UUID
    section_id: UUID
    section_ordinal: int
    chunk_ordinal: int
    heading: str | None
    locator: dict[str, Any]
    char_start: int
    char_end: int
    text: str
    score: float


class ResearchSearchResponse(BaseModel):
    query: str
    selected_library_entry_ids: list[UUID]
    passages: list[ResearchPassageResponse]


class GroundedResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1_000)
    library_entry_ids: list[UUID] = Field(min_length=1, max_length=20)
    max_passages: int = Field(default=12, ge=1, le=30)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Grounded research question cannot be blank")
        return normalized

    @field_validator("library_entry_ids")
    @classmethod
    def require_unique_entries(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Selected library entries must be unique")
        return value


class GroundedClaimDraft(BaseModel):
    text: str = Field(min_length=1, max_length=1_200)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=8)

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Grounded claim cannot be blank")
        return normalized

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Grounded claim evidence IDs must be unique")
        return value


class GroundedAnswerDraft(BaseModel):
    claims: list[GroundedClaimDraft] = Field(default_factory=list, max_length=10)
    insufficient_evidence: bool = False

    @model_validator(mode="after")
    def validate_evidence_state(self) -> "GroundedAnswerDraft":
        if self.insufficient_evidence and self.claims:
            raise ValueError("Insufficient-evidence responses cannot include claims")
        if not self.insufficient_evidence and not self.claims:
            raise ValueError("Grounded responses require at least one cited claim")
        return self


class GroundedCitationResponse(BaseModel):
    evidence_id: UUID
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    edition_id: UUID
    edition_title: str
    document_id: UUID
    chunk_id: UUID
    section_id: UUID
    heading: str | None
    locator: dict[str, Any]
    char_start: int
    char_end: int
    text: str


class GroundedClaimResponse(BaseModel):
    text: str
    citations: list[GroundedCitationResponse]


class GroundedAnswerStatus(StrEnum):
    GROUNDED = "grounded"
    NO_EVIDENCE = "no_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class GroundedAnswerResponse(BaseModel):
    status: GroundedAnswerStatus
    question: str
    selected_library_entry_ids: list[UUID]
    retrieval_count: int
    claims: list[GroundedClaimResponse]
    provider: str | None
    model: str | None
