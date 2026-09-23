import datetime
import typing
import uuid

import pydantic


type ReadingStatus = typing.Literal["unread", "reading", "finished"]


class PortableIdentifier(pydantic.BaseModel):
    scheme: str
    value: str


class PortableSourceReference(pydantic.BaseModel):
    provider: str
    provider_record_id: str
    canonical_url: str
    relationship: str


class PortableWorkIdentity(pydantic.BaseModel):
    source_work_id: uuid.UUID
    canonical_title: str
    authors: list[str]
    subjects: list[str]
    identifiers: list[PortableIdentifier]
    sources: list[PortableSourceReference]


class PortableEditionIdentity(pydantic.BaseModel):
    source_edition_id: uuid.UUID
    title: str
    language: str | None
    publication_year: int | None
    publisher: str | None
    edition_statement: str | None
    identifiers: list[PortableIdentifier]
    sources: list[PortableSourceReference]


class PortableRightsEvidence(pydantic.BaseModel):
    state: str
    source: str
    basis: str
    evidence_url: str | None
    license_uri: str | None
    confidence: float = pydantic.Field(ge=0, le=1)


class PortableRightsSnapshot(pydantic.BaseModel):
    rights_state: str
    jurisdiction: str
    policy_version: str
    permissions: dict[str, bool]
    reason: str
    evaluated_at: datetime.datetime
    evidence: list[PortableRightsEvidence]


class PortableDocumentIdentity(pydantic.BaseModel):
    source_document_id: uuid.UUID
    source_sha256: str
    format: str
    parser_name: str
    parser_version: str


class PortableBytePolicy(pydantic.BaseModel):
    bytes_included: typing.Literal[False] = False
    policy_export_allowed: bool
    policy_share_allowed: bool


class PortableAssetManifest(pydantic.BaseModel):
    source_asset_id: uuid.UUID
    edition: PortableEditionIdentity
    format: str
    media_type: str | None
    byte_size: int | None = pydantic.Field(default=None, ge=0)
    content_sha256: str | None
    identifiers: list[PortableIdentifier]
    sources: list[PortableSourceReference]
    document: PortableDocumentIdentity | None
    rights: PortableRightsSnapshot | None
    byte_policy: PortableBytePolicy


class PortableReadingPosition(pydantic.BaseModel):
    section_ordinal: int = pydantic.Field(ge=0)
    char_offset: int | None = pydantic.Field(default=None, ge=0)
    locator: dict[str, pydantic.JsonValue]


class PortableBookmark(pydantic.BaseModel):
    source_bookmark_id: uuid.UUID
    section_ordinal: int = pydantic.Field(ge=0)
    char_offset: int = pydantic.Field(ge=0)
    locator: dict[str, pydantic.JsonValue]
    label: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PortableHighlight(pydantic.BaseModel):
    source_highlight_id: uuid.UUID
    section_ordinal: int = pydantic.Field(ge=0)
    char_start: int = pydantic.Field(ge=0)
    char_end: int = pydantic.Field(gt=0)
    locator: dict[str, pydantic.JsonValue]
    note: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PortableReadingState(pydantic.BaseModel):
    source_reading_state_id: uuid.UUID
    document: PortableDocumentIdentity
    status: ReadingStatus
    progress_fraction: float = pydantic.Field(ge=0, le=1)
    position: PortableReadingPosition | None
    last_read_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
    bookmarks: list[PortableBookmark]
    highlights: list[PortableHighlight]


class PortableLibraryEntry(pydantic.BaseModel):
    source_library_entry_id: uuid.UUID
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    work: PortableWorkIdentity
    edition: PortableEditionIdentity | None
    assets: list[PortableAssetManifest]
    reading_states: list[PortableReadingState]
    collection_ids: list[uuid.UUID]
    tag_ids: list[uuid.UUID]


class PortableCollection(pydantic.BaseModel):
    source_collection_id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PortableTag(pydantic.BaseModel):
    source_tag_id: uuid.UUID
    name: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PortableSmartShelf(pydantic.BaseModel):
    source_smart_shelf_id: uuid.UUID
    name: str
    description: str | None
    reading_status: ReadingStatus | None
    collection_id: uuid.UUID | None
    tag_id: uuid.UUID | None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    @pydantic.model_validator(mode="after")
    def require_rule(self) -> "PortableSmartShelf":
        if self.reading_status is None and self.collection_id is None and self.tag_id is None:
            raise ValueError("smart shelf must define at least one rule")
        return self


class LibraryPortabilityExportResponse(pydantic.BaseModel):
    schema_version: typing.Literal[1] = 1
    export_kind: typing.Literal["bukmatika-library-manifest"] = "bukmatika-library-manifest"
    content_mode: typing.Literal["metadata-and-state-only"] = "metadata-and-state-only"
    exported_at: datetime.datetime
    entries: list[PortableLibraryEntry]
    collections: list[PortableCollection]
    tags: list[PortableTag]
    smart_shelves: list[PortableSmartShelf]


type ImportPlanAction = typing.Literal["match", "create", "apply", "skip", "conflict"]
type ImportPlanTarget = typing.Literal[
    "work",
    "edition",
    "library_entry",
    "document",
    "reading_state",
    "collection",
    "tag",
    "smart_shelf",
]


class PortableImportTargetPlan(pydantic.BaseModel):
    target: ImportPlanTarget
    source_id: uuid.UUID
    action: ImportPlanAction
    destination_id: uuid.UUID | None = None
    reason: str


class PortableImportConflict(pydantic.BaseModel):
    target: ImportPlanTarget
    source_id: uuid.UUID
    code: str
    detail: str


class PortableImportEntryPlan(pydantic.BaseModel):
    source_library_entry_id: uuid.UUID
    work: PortableImportTargetPlan
    edition: PortableImportTargetPlan | None
    library_entry: PortableImportTargetPlan
    documents: list[PortableImportTargetPlan]
    reading_states: list[PortableImportTargetPlan]
    collection_ids: list[uuid.UUID]
    tag_ids: list[uuid.UUID]


class LibraryPortabilityImportPlanResponse(pydantic.BaseModel):
    schema_version: typing.Literal[1] = 1
    mode: typing.Literal["dry-run"] = "dry-run"
    can_apply: bool
    entries: list[PortableImportEntryPlan]
    collections: list[PortableImportTargetPlan]
    tags: list[PortableImportTargetPlan]
    smart_shelves: list[PortableImportTargetPlan]
    conflicts: list[PortableImportConflict]
