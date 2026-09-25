from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetStatusResponse(BaseModel):
    asset_id: UUID
    format: str
    media_type: str | None
    byte_size: int | None = Field(default=None, ge=0)
    stored: bool
    acquisition_id: UUID | None
    acquisition_status: str | None
    acquisition_request_id: UUID | None = None
    acquisition_request_status: str | None = None
    acquisition_approval_mode: str | None = None
    processing_status: str | None
    processing_error_code: str | None
    ocr_job_id: UUID | None
    ocr_job_status: str | None
    document_id: UUID | None
    rights_state: str | None
    acquisition_allowed: bool | None


class EditionDossierResponse(BaseModel):
    edition_id: UUID
    title: str
    language: str | None
    publication_year: int | None
    publisher: str | None
    edition_statement: str | None
    library_entry_id: UUID | None
    assets: list[AssetStatusResponse]


class WorkDossierResponse(BaseModel):
    work_id: UUID
    title: str
    authors: list[str]
    subjects: list[str]
    work_library_entry_id: UUID | None
    editions: list[EditionDossierResponse]


class LibraryReadingStatus(StrEnum):
    UNREAD = "unread"
    READING = "reading"
    FINISHED = "finished"


class LibraryAssetAggregateStatus(StrEnum):
    READY = "ready"
    IN_PROGRESS = "in_progress"
    NEEDS_ACTION = "needs_action"
    FAILED = "failed"


class LibraryAssetStage(StrEnum):
    RIGHTS = "rights"
    ACQUISITION = "acquisition"
    PROCESSING = "processing"
    OCR = "ocr"
    READY = "ready"


class LibraryAssetStatusItem(BaseModel):
    library_entry_id: UUID
    work_id: UUID
    work_title: str
    edition_id: UUID
    edition_title: str
    asset_id: UUID
    format: str
    media_type: str | None
    aggregate_status: LibraryAssetAggregateStatus
    stage: LibraryAssetStage
    status_code: str
    rights_state: str | None
    acquisition_allowed: bool | None
    acquisition_id: UUID | None
    acquisition_status: str | None
    acquisition_job_id: UUID | None
    acquisition_job_status: str | None
    acquisition_error_code: str | None
    processing_status: str | None
    processing_error_code: str | None
    ocr_job_id: UUID | None
    ocr_job_status: str | None
    ocr_error_code: str | None
    document_id: UUID | None


class LibraryStatusSummary(BaseModel):
    total: int = Field(ge=0)
    ready: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    needs_action: int = Field(ge=0)
    failed: int = Field(ge=0)


class LibraryStatusResponse(BaseModel):
    summary: LibraryStatusSummary
    items: list[LibraryAssetStatusItem]


class CollectionSummaryResponse(BaseModel):
    collection_id: UUID
    name: str


class TagSummaryResponse(BaseModel):
    tag_id: UUID
    name: str


class LibraryItemResponse(BaseModel):
    library_entry_id: UUID
    work_id: UUID
    edition_id: UUID | None
    title: str
    authors: list[str]
    status: str
    readable_document_id: UUID | None
    readable_format: str | None
    progress_fraction: float | None = Field(default=None, ge=0, le=1)
    reading_status: str | None
    collections: list[CollectionSummaryResponse] = Field(default_factory=list)
    tags: list[TagSummaryResponse] = Field(default_factory=list)


class LibraryResponse(BaseModel):
    items: list[LibraryItemResponse]


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Collection name cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class CollectionUpdate(CollectionCreate):
    pass


class CollectionResponse(BaseModel):
    collection_id: UUID
    name: str
    description: str | None
    item_count: int = Field(ge=0)


class TagAssignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Tag name cannot be blank")
        return normalized


class TagUpdate(TagAssignRequest):
    pass


class SmartShelfRule(BaseModel):
    reading_status: LibraryReadingStatus | None = None
    collection_id: UUID | None = None
    tag_id: UUID | None = None

    @model_validator(mode="after")
    def require_predicate(self) -> Self:
        if self.reading_status is None and self.collection_id is None and self.tag_id is None:
            raise ValueError("Smart shelf requires at least one rule predicate")
        return self


class SmartShelfCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    rule: SmartShelfRule

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Smart shelf name cannot be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class SmartShelfUpdate(SmartShelfCreate):
    pass


class SmartShelfResponse(BaseModel):
    smart_shelf_id: UUID
    name: str
    description: str | None
    rule: SmartShelfRule
    item_count: int = Field(ge=0)


class SmartShelfContentsResponse(BaseModel):
    shelf: SmartShelfResponse
    items: list[LibraryItemResponse]


class LibraryOrganizationResponse(BaseModel):
    collections: list[CollectionResponse]
    tags: list[TagResponse]
    smart_shelves: list[SmartShelfResponse] = Field(default_factory=list)
