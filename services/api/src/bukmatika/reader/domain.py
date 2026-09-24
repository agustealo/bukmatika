from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ReaderSection(BaseModel):
    section_id: UUID
    ordinal: int = Field(ge=0)
    heading: str | None
    locator: dict[str, Any]
    text: str


class ReaderNavigationKind(StrEnum):
    PDF_PAGES = "pdf_pages"
    EPUB_SPINE = "epub_spine"


class ReaderNavigationItem(BaseModel):
    key: str
    label: str
    section_id: UUID
    section_ordinal: int = Field(ge=0)
    locator: dict[str, Any]
    heading: str | None = None


class ReaderNavigationResponse(BaseModel):
    format: str
    kind: ReaderNavigationKind | None
    items: list[ReaderNavigationItem]


class ReadingStateResponse(BaseModel):
    reading_state_id: UUID
    library_entry_id: UUID
    document_id: UUID
    status: str
    progress_fraction: float = Field(ge=0, le=1)
    section_id: UUID | None
    section_ordinal: int | None = Field(default=None, ge=0)
    char_offset: int | None = Field(default=None, ge=0)
    locator: dict[str, Any]


class BookmarkResponse(BaseModel):
    bookmark_id: UUID
    section_id: UUID
    char_offset: int = Field(ge=0)
    locator: dict[str, Any]
    label: str | None


class HighlightResponse(BaseModel):
    highlight_id: UUID
    section_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    locator: dict[str, Any]
    text: str
    note: str | None


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
    highlights: list[HighlightResponse]
    sections: list[ReaderSection]
    next_after_ordinal: int | None = Field(default=None, ge=0)


class ReadingProgressUpdate(BaseModel):
    section_id: UUID
    char_offset: int = Field(ge=0)


class BookmarkCreate(BaseModel):
    section_id: UUID
    char_offset: int = Field(ge=0)
    label: str | None = Field(default=None, max_length=200)


class HighlightCreate(BaseModel):
    section_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def validate_range(self) -> "HighlightCreate":
        if self.char_end <= self.char_start:
            raise ValueError("Highlight end must be after its start")
        return self


class HighlightNoteUpdate(BaseModel):
    note: str | None = Field(default=None, max_length=4_000)
