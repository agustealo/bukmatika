from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


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
