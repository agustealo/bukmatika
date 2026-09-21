from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, field_validator


class RightsState(StrEnum):
    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    AUTHORIZED_DOWNLOAD = "authorized_download"
    BORROW_ONLY = "borrow_only"
    PREVIEW_ONLY = "preview_only"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


class RightsEvidence(BaseModel):
    state: RightsState
    source: str
    basis: str
    evidence_url: HttpUrl | None = None
    license_uri: HttpUrl | None = None
    confidence: Annotated[float, Field(ge=0, le=1)] = 1.0


class SearchIntent(BaseModel):
    query: str | None = None
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    language: str | None = None
    year_from: int | None = Field(default=None, ge=1, le=3000)
    year_to: int | None = Field(default=None, ge=1, le=3000)
    limit: int = Field(default=24, ge=1, le=100)

    @field_validator("query", "title", "author", "subject", "language", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = " ".join(value.split())
            return cleaned or None
        return value

    def model_post_init(self, __context: object) -> None:
        if not any((self.query, self.title, self.author, self.subject)):
            raise ValueError("At least one search term is required")
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from cannot be greater than year_to")


class DiscoveryCandidate(BaseModel):
    source: str
    source_record_id: str
    work_key: str
    edition_keys: list[str] = Field(default_factory=list)
    title: str
    authors: list[str] = Field(default_factory=list)
    first_publish_year: int | None = None
    languages: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    landing_url: HttpUrl
    formats: list[str] = Field(default_factory=list)
    rights: list[RightsEvidence] = Field(default_factory=list)
    source_score: Annotated[float, Field(ge=0, le=1)] = 0.5


class DiscoveryResponse(BaseModel):
    intent: SearchIntent
    candidates: list[DiscoveryCandidate]
    sources_queried: list[str]
    source_errors: dict[str, str] = Field(default_factory=dict)
