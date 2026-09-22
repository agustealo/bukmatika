from dataclasses import dataclass
from typing import TypeAlias
from uuid import UUID

from pydantic import BaseModel, Field

DocumentLocator: TypeAlias = dict[str, str | int]


@dataclass(frozen=True, slots=True)
class ParsedSection:
    ordinal: int
    heading: str | None
    locator: DocumentLocator
    text: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    parser_name: str
    parser_version: str
    sections: tuple[ParsedSection, ...]


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    ordinal: int
    section_ordinal: int
    char_start: int
    char_end: int
    text: str


class DocumentResponse(BaseModel):
    document_id: UUID
    asset_id: UUID
    stored_object_id: UUID
    source_sha256: str
    format: str
    parser_name: str
    parser_version: str
    section_count: int = Field(ge=1)
    chunk_count: int = Field(ge=1)


class DocumentSearchHit(BaseModel):
    chunk_id: UUID
    section_id: UUID
    section_ordinal: int = Field(ge=0)
    chunk_ordinal: int = Field(ge=0)
    heading: str | None
    locator: DocumentLocator
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text: str
    score: float


class DocumentSearchResponse(BaseModel):
    document_id: UUID
    query: str
    items: list[DocumentSearchHit]
