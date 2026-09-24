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
    autonomy_level: int = Field(ge=0, le=2)
    level_2_consent: bool = False


class PersonalizationProfileResponse(BaseModel):
    user_model_id: UUID
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int
    active_preferences: list[PreferenceClaimResponse]


class ContextTask(StrEnum):
    DISCOVERY = "discovery"
    LIBRARY = "library"
    RESEARCH = "research"
    READER = "reader"


class ContextScope(BaseModel):
    scope_type: PreferenceScopeType
    scope_value: str = Field(max_length=255)

    @field_validator("scope_value")
    @classmethod
    def normalize_scope_value(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def validate_scope(self) -> "ContextScope":
        if self.scope_type is PreferenceScopeType.GLOBAL:
            raise ValueError("Global context scope is implicit")
        if not self.scope_value:
            raise ValueError("Context scope values cannot be empty")
        return self


class ContextRequest(BaseModel):
    task: ContextTask
    goal_id: UUID | None = None
    library_entry_ids: list[UUID] = Field(default_factory=list, max_length=20)
    scopes: list[ContextScope] = Field(default_factory=list, max_length=12)

    @field_validator("library_entry_ids")
    @classmethod
    def deduplicate_library_entries(cls, values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_scopes(self) -> "ContextRequest":
        keys = [(scope.scope_type, scope.scope_value) for scope in self.scopes]
        if len(keys) != len(set(keys)):
            raise ValueError("Context scopes must be unique")
        return self


class ContextPreference(BaseModel):
    claim_id: UUID
    key: PreferenceKey
    value: dict[str, Any]
    source: str
    confidence: float
    scope_type: PreferenceScopeType
    scope_value: str
    influence: PreferenceInfluence
    inclusion_reason: str


class ContextGoal(BaseModel):
    goal_id: UUID
    title: str
    kind: str
    scope: dict[str, Any]
    constraints: dict[str, Any]
    inclusion_reason: str


class ContextLibraryEntry(BaseModel):
    library_entry_id: UUID
    work_id: UUID
    edition_id: UUID | None
    title: str
    document_ids: list[UUID]
    inclusion_reason: str


class ContextManifest(BaseModel):
    task: ContextTask
    ai_enabled: bool
    learning_enabled: bool
    autonomy_level: int
    model_context_ready: bool
    preferences: list[ContextPreference]
    goal: ContextGoal | None
    library_entries: list[ContextLibraryEntry]
    available_capabilities: list[str]
    exclusion_reasons: list[str]