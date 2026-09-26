from collections.abc import Callable
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, computed_field, field_validator


class RightsState(StrEnum):
    PUBLIC_DOMAIN = "public_domain"
    OPEN_LICENSE = "open_license"
    AUTHORIZED_DOWNLOAD = "authorized_download"
    BORROW_ONLY = "borrow_only"
    PREVIEW_ONLY = "preview_only"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


_LANGUAGE_ALIASES: dict[str, str] = {
    "de": "de",
    "deu": "de",
    "eng": "en",
    "english": "en",
    "en": "en",
    "es": "es",
    "fra": "fr",
    "fre": "fr",
    "fr": "fr",
    "french": "fr",
    "ger": "de",
    "german": "de",
    "ita": "it",
    "italian": "it",
    "it": "it",
    "la": "la",
    "lat": "la",
    "latin": "la",
    "por": "pt",
    "portuguese": "pt",
    "pt": "pt",
    "spa": "es",
    "spanish": "es",
}


def canonical_language(value: str) -> str:
    cleaned = " ".join(value.split()).casefold()
    return _LANGUAGE_ALIASES.get(cleaned, cleaned)


def canonical_format(value: str) -> str:
    return " ".join(value.split()).casefold()


def canonical_source(value: str) -> str:
    return "_".join(value.strip().casefold().replace("-", " ").split())


def _unique_nonempty(values: list[str], normalizer: Callable[[str], str]) -> list[str]:
    normalized = [normalizer(value) for value in values]
    return list(dict.fromkeys(value for value in normalized if value))


class DiscoveryPreferences(BaseModel):
    languages: list[str] = Field(default_factory=list, max_length=12)
    formats: list[str] = Field(default_factory=list, max_length=12)
    year_from: int | None = Field(default=None, ge=1, le=3000)
    year_to: int | None = Field(default=None, ge=1, le=3000)
    rights_states: list[RightsState] = Field(default_factory=list, max_length=7)
    sources: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("languages")
    @classmethod
    def normalize_languages(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values, canonical_language)

    @field_validator("formats")
    @classmethod
    def normalize_formats(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values, canonical_format)

    @field_validator("sources")
    @classmethod
    def normalize_sources(cls, values: list[str]) -> list[str]:
        return _unique_nonempty(values, canonical_source)

    @field_validator("rights_states")
    @classmethod
    def normalize_rights_states(cls, values: list[RightsState]) -> list[RightsState]:
        return list(dict.fromkeys(values))

    def model_post_init(self, __context: object) -> None:
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("preferences.year_from cannot be greater than preferences.year_to")


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
    preferences: DiscoveryPreferences = Field(default_factory=DiscoveryPreferences)
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
    rights: list[RightsEvidence] = Field(default_factory=list)


class DiscoveredCover(BaseModel):
    url: HttpUrl
    kind: Literal["cover", "thumbnail"] = "cover"
    media_type: str | None = None
    width: int | None = Field(default=None, ge=1, le=20_000)
    height: int | None = Field(default=None, ge=1, le=20_000)


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
    covers: list[DiscoveredCover] = Field(default_factory=list, max_length=8, exclude=True)
    rights: list[RightsEvidence] = Field(default_factory=list)
    source_score: Annotated[float, Field(ge=0, le=1)] = 0.5

    @computed_field
    @property
    def has_cover(self) -> bool:
        return bool(self.covers)


DiscoveryPreferenceDimension = Literal["language", "format", "era", "rights", "source"]


class DiscoveryRankingExplanation(BaseModel):
    neutral_score: float = Field(ge=0)
    preference_boost: float = Field(ge=0, le=0.05)
    total_score: float = Field(ge=0)
    matched_preferences: list[DiscoveryPreferenceDimension] = Field(default_factory=list)


class DiscoverySourceStatus(BaseModel):
    status: Literal["ok", "error", "timeout", "rate_limited"]
    elapsed_ms: int = Field(ge=0)
    result_count: int = Field(ge=0)
    error_code: Literal[
        "timeout",
        "rate_limited",
        "http_error",
        "transport_error",
        "provider_error",
    ] | None = None
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)


class DiscoveryResponse(BaseModel):
    session_id: UUID
    elapsed_ms: int = Field(ge=0)
    intent: SearchIntent
    candidates: list[DiscoveryCandidate]
    ranking: dict[str, DiscoveryRankingExplanation] = Field(default_factory=dict)
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
