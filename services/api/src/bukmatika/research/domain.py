import re
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_READER_SELECTION_CHARS = 6_000
_EVIDENCE_ID_PATTERN = re.compile(r"^E[1-9][0-9]*$")


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


class ResearchCompareRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    library_entry_ids: list[UUID] = Field(min_length=2, max_length=6)
    per_source_limit: int = Field(default=3, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Comparison query cannot be blank")
        return normalized

    @field_validator("library_entry_ids")
    @classmethod
    def require_unique_entries(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Comparison sources must be unique")
        return value


class ResearchComparisonEdition(BaseModel):
    edition_id: UUID
    edition_title: str


class ResearchComparisonSource(BaseModel):
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    available_editions: list[ResearchComparisonEdition] = Field(min_length=1)
    passages: list[ResearchPassageResponse]


class ResearchCompareResponse(BaseModel):
    query: str
    selected_library_entry_ids: list[UUID]
    per_source_limit: int
    sources: list[ResearchComparisonSource] = Field(min_length=2, max_length=6)


class ReaderResearchContextRequest(BaseModel):
    library_entry_id: UUID
    document_id: UUID
    section_id: UUID
    char_offset: int = Field(default=0, ge=0)
    selection_start: int | None = Field(default=None, ge=0)
    selection_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_selection(self) -> "ReaderResearchContextRequest":
        if (self.selection_start is None) != (self.selection_end is None):
            raise ValueError("Reader selection start and end must be provided together")
        if self.selection_start is None or self.selection_end is None:
            return self
        if self.selection_start >= self.selection_end:
            raise ValueError("Reader selection must have positive length")
        if self.selection_end - self.selection_start > MAX_READER_SELECTION_CHARS:
            raise ValueError("Reader selection exceeds the grounding character budget")
        if not self.selection_start <= self.char_offset <= self.selection_end:
            raise ValueError("Reader character offset must fall inside the explicit selection")
        return self


class ResearchEvidenceBundleRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    reader: ReaderResearchContextRequest
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

    @model_validator(mode="after")
    def require_reader_entry_selected(self) -> "ResearchEvidenceBundleRequest":
        if self.reader.library_entry_id not in self.library_entry_ids:
            raise ValueError("The current reader book must be in the research selection")
        return self


class ResearchEvidenceSourceKind(StrEnum):
    READER_POSITION = "reader_position"
    READER_SELECTION = "reader_selection"
    RELATED_PASSAGE = "related_passage"


class ResearchEvidenceItem(BaseModel):
    evidence_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    source_kind: ResearchEvidenceSourceKind
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    edition_id: UUID
    edition_title: str
    asset_id: UUID
    document_id: UUID
    chunk_id: UUID
    section_id: UUID
    section_ordinal: int = Field(ge=0)
    chunk_ordinal: int = Field(ge=0)
    heading: str | None
    locator: dict[str, Any]
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    text: str = Field(min_length=1)
    score: float | None = None


class ResearchEvidenceBundleResponse(BaseModel):
    question: str
    reader: ReaderResearchContextRequest
    selected_library_entry_ids: list[UUID]
    evidence: list[ResearchEvidenceItem] = Field(min_length=1, max_length=38)


class GroundedAnswerClaim(BaseModel):
    text: str = Field(min_length=1, max_length=1_000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("Grounded claim evidence IDs must be unique")
        if any(_EVIDENCE_ID_PATTERN.fullmatch(value) is None for value in values):
            raise ValueError("Grounded claim evidence IDs must use the request-local E# format")
        return values


class GroundedResearchAnswer(BaseModel):
    claims: list[GroundedAnswerClaim] = Field(min_length=1, max_length=20)
