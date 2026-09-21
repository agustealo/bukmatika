from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

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


class DiscoveredAsset(BaseModel):
    name: str
    url: HttpUrl
    format: str
    media_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    source_kind: str | None = None
    checksums: dict[str, str] = Field(default_factory=dict)


class DiscoveryCandidate(BaseModel):
    source: str
    source_record_id: str
    record_kind: Literal["work", "edition"] = "work"
    work_key: str
    edition_keys: list[str] = Field(default_factory=list)
    identifiers: dict[str, list[str]] = Field(default_factory=dict)
    title: str
    authors: list[str] = Field(default_factory=list)
    first_publish_year: int | None = None
    publisher: str | None = None
    languages: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    landing_url: HttpUrl
    formats: list[str] = Field(default_factory=list)
    assets: list[DiscoveredAsset] = Field(default_factory=list)
    rights: list[RightsEvidence] = Field(default_factory=list)
    source_score: Annotated[float, Field(ge=0, le=1)] = 0.5


class DiscoverySourceStatus(BaseModel):
    status: Literal["ok", "error", "timeout"]
    elapsed_ms: int = Field(ge=0)
    result_count: int = Field(ge=0)


class DiscoveryResponse(BaseModel):
    session_id: UUID
    elapsed_ms: int = Field(ge=0)
    intent: SearchIntent
    candidates: list[DiscoveryCandidate]
    sources_queried: list[str]
    source_errors: dict[str, str] = Field(default_factory=dict)
    source_status: dict[str, DiscoverySourceStatus] = Field(default_factory=dict)


class CatalogSearchItem(BaseModel):
    work_id: UUID
    title: str
    authors: list[str] = Field(default_factory=list)
    score: float = Field(ge=0)


class CatalogSearchResponse(BaseModel):
    query: str
    items: list[CatalogSearchItem]
