from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class PreferenceKey(StrEnum):
    FORMAT_PREFERRED = "format.preferred"
    LANGUAGE_PREFERRED = "language.preferred"
    EDITION_AGE = "edition.age_preference"
    RESEARCH_SOURCE_TYPE = "research.source_type"
    READER_EXPLANATION_DEPTH = "reader.explanation_depth"
    CITATION_STYLE = "citation.style"
    OCR_SCAN_ACCEPTANCE = "acquisition.ocr_scans"
    AUTO_COLLECTIONS = "library.auto_collections"
    AUTO_DOWNLOAD = "acquisition.auto_download"
    SUBJECT_INTERESTS = "subject.interests"
    PERIOD_INTERESTS = "period.interests"
    AUTHOR_INTERESTS = "author.interests"
    REGION_INTERESTS = "region.interests"
    SOURCE_INSTITUTIONS = "source.institutions"


class PreferenceScopeType(StrEnum):
    GLOBAL = "global"
    DISCOVERY = "discovery"
    RESEARCH = "research"
    READER = "reader"
    SUBJECT = "subject"
    COLLECTION = "collection"
    FORMAT = "format"
    DEVICE = "device"
    GOAL = "goal"


class PreferenceInfluence(BaseModel):
    ranking: bool = True
    presentation: bool = True
    automation: bool = False


class ExplicitPreferenceRequest(BaseModel):
    key: PreferenceKey
    value: dict[str, Any]
    scope_type: PreferenceScopeType = PreferenceScopeType.GLOBAL
    scope_value: str = Field(default="", max_length=255)
    influence: PreferenceInfluence = Field(default_factory=PreferenceInfluence)

    @field_validator("scope_value")
    @classmethod
    def normalize_scope_value(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def validate_scope(self) -> "ExplicitPreferenceRequest":
        if self.scope_type is PreferenceScopeType.GLOBAL and self.scope_value:
            raise ValueError("Global preferences cannot have a scope value")
        if self.scope_type is not PreferenceScopeType.GLOBAL and not self.scope_value:
            raise ValueError("Scoped preferences require a scope value")
        return self


class PreferenceClaimResponse(BaseModel):
    claim_id: UUID
    key: PreferenceKey
    value: dict[str, Any]
    source: str
    status: str
    confidence: float
    scope_type: PreferenceScopeType
    scope_value: str
    influence: PreferenceInfluence
    evidence_count: int
    first_observed_at: datetime
    last_reinforced_at: datetime
    created_at: datetime
    updated_at: datetime


class PersonalizationSettingsUpdate(BaseModel):
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int = Field(ge=0, le=1)


class PersonalizationProfileResponse(BaseModel):
    user_model_id: UUID
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int
    active_preferences: list[PreferenceClaimResponse]
