from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ReaderSection(BaseModel):
    section_id: UUID
    ordinal: int = Field(ge=0)
    heading: str | None
    locator: dict[str, Any]
    text: str


class ReadingStateResponse(BaseModel):
    reading_state_id: UUID
    library_entry_id: UUID
    document_id: UUID
    status: str
    progress_fraction: float = Field(ge=0, le=1)
    section_id: UUID | None
    char_offset: int | None = Field(default=None, ge=0)
    locator: dict[str, Any]


class BookmarkResponse(BaseModel):
    bookmark_id: UUID
    section_id: UUID
    char_offset: int = Field(ge=0)
    locator: dict[str, Any]
    label: str | None


class ReaderDocumentResponse(BaseModel):
    library_entry_id: UUID
    document_id: UUID
    asset_id: UUID
    format: str
    parser_name: str
    parser_version: str
    section_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)
    reading_state: ReadingStateResponse | None
    bookmarks: list[BookmarkResponse]
    sections: list[ReaderSection]
    next_after_ordinal: int | None = Field(default=None, ge=0)


class ReadingProgressUpdate(BaseModel):
    section_id: UUID
    char_offset: int = Field(ge=0)
    progress_fraction: float = Field(ge=0, le=1)


class BookmarkCreate(BaseModel):
    section_id: UUID
    char_offset: int = Field(ge=0)
    label: str | None = Field(default=None, max_length=200)
