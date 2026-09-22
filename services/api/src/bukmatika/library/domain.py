from uuid import UUID

from pydantic import BaseModel, Field


class AssetStatusResponse(BaseModel):
    asset_id: UUID
    format: str
    media_type: str | None
    byte_size: int | None = Field(default=None, ge=0)
    stored: bool
    acquisition_id: UUID | None
    acquisition_status: str | None
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


class LibraryResponse(BaseModel):
    items: list[LibraryItemResponse]
