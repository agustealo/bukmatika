from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue


class PortableIdentifier(BaseModel):
    scheme: str
    value: str


class PortableSourceReference(BaseModel):
    provider: str
    provider_record_id: str
    canonical_url: str
    relationship: str


class PortableWorkIdentity(BaseModel):
    source_work_id: UUID
    canonical_title: str
    authors: list[str]
    subjects: list[str]
    identifiers: list[PortableIdentifier]
    sources: list[PortableSourceReference]


class PortableEditionIdentity(BaseModel):
    source_edition_id: UUID
    title: str
    language: str | None
    publication_year: int | None
    publisher: str | None
    edition_statement: str | None
    identifiers: list[PortableIdentifier]
    sources: list[PortableSourceReference]


class PortableRightsEvidence(BaseModel):
    state: str
    source: str
    basis: str
    evidence_url: str | None
    license_uri: str | None
    confidence: float = Field(ge=0, le=1)


class PortableRightsSnapshot(BaseModel):
    rights_state: str
    jurisdiction: str
    policy_version: str
    permissions: dict[str, bool]
    reason: str
    evaluated_at: datetime
    evidence: list[PortableRightsEvidence]


class PortableDocumentIdentity(BaseModel):
    source_document_id: UUID
    source_sha256: str
    format: str
    parser_name: str
    parser_version: str


class PortableBytePolicy(BaseModel):
    bytes_included: Literal[False] = False
    policy_export_allowed: bool
    policy_share_allowed: bool


class PortableAssetManifest(BaseModel):
    source_asset_id: UUID
    edition: PortableEditionIdentity
    format: str
    media_type: str | None
    byte_size: int | None = Field(default=None, ge=0)
    content_sha256: str | None
    identifiers: list[PortableIdentifier]
    sources: list[PortableSourceReference]
    document: PortableDocumentIdentity | None
    rights: PortableRightsSnapshot | None
    byte_policy: PortableBytePolicy


class PortableReadingPosition(BaseModel):
    section_ordinal: int = Field(ge=0)
    char_offset: int | None = Field(default=None, ge=0)
    locator: dict[str, JsonValue]


class PortableBookmark(BaseModel):
    source_bookmark_id: UUID
    section_ordinal: int = Field(ge=0)
    char_offset: int = Field(ge=0)
    locator: dict[str, JsonValue]
    label: str | None
    created_at: datetime
    updated_at: datetime


class PortableHighlight(BaseModel):
    source_highlight_id: UUID
    section_ordinal: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    locator: dict[str, JsonValue]
    note: str | None
    created_at: datetime
    updated_at: datetime


class PortableReadingState(BaseModel):
    source_reading_state_id: UUID
    document: PortableDocumentIdentity
    status: str
    progress_fraction: float = Field(ge=0, le=1)
    position: PortableReadingPosition | None
    last_read_at: datetime | None
    created_at: datetime
    updated_at: datetime
    bookmarks: list[PortableBookmark]
    highlights: list[PortableHighlight]


class PortableLibraryEntry(BaseModel):
    source_library_entry_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
    work: PortableWorkIdentity
    edition: PortableEditionIdentity | None
    assets: list[PortableAssetManifest]
    reading_states: list[PortableReadingState]
    collection_ids: list[UUID]
    tag_ids: list[UUID]


class PortableCollection(BaseModel):
    source_collection_id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class PortableTag(BaseModel):
    source_tag_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class PortableSmartShelf(BaseModel):
    source_smart_shelf_id: UUID
    name: str
    description: str | None
    reading_status: str | None
    collection_id: UUID | None
    tag_id: UUID | None
    created_at: datetime
    updated_at: datetime


class LibraryPortabilityExportResponse(BaseModel):
    schema_version: Literal[1] = 1
    export_kind: Literal["bukmatika-library-manifest"] = "bukmatika-library-manifest"
    content_mode: Literal["metadata-and-state-only"] = "metadata-and-state-only"
    exported_at: datetime
    entries: list[PortableLibraryEntry]
    collections: list[PortableCollection]
    tags: list[PortableTag]
    smart_shelves: list[PortableSmartShelf]
