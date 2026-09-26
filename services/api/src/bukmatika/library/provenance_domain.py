from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MetadataAssertionResponse(BaseModel):
    field_name: str
    value: Any
    provider: str
    provider_record_id: str
    source_url: str
    confidence: float = Field(ge=0, le=1)
    normalization_method: str | None
    parser_version: str
    first_observed_at: datetime
    last_observed_at: datetime
    observation_count: int = Field(ge=1)
    assertion_created_at: datetime


class EditionMetadataProvenanceResponse(BaseModel):
    edition_id: UUID
    title: str
    assertions: list[MetadataAssertionResponse]


class WorkMetadataProvenanceResponse(BaseModel):
    work_id: UUID
    title: str
    assertions: list[MetadataAssertionResponse]
    editions: list[EditionMetadataProvenanceResponse]


__all__ = [
    "EditionMetadataProvenanceResponse",
    "MetadataAssertionResponse",
    "WorkMetadataProvenanceResponse",
]
