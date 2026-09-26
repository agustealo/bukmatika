from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from bukmatika.library.portability_domain import LibraryPortabilityExportResponse
from bukmatika.personalization.portability_domain import PersonalizationExportResponse


class PrincipalExport(BaseModel):
    principal_id: UUID
    kind: str
    created_at: datetime
    updated_at: datetime


class OperationalRecordExport(BaseModel):
    record_type: str
    record_id: str
    data: dict[str, JsonValue]


class AccountPrivacyExportResponse(BaseModel):
    schema_version: Literal[1] = 1
    export_kind: Literal["bukmatika-account-data"] = "bukmatika-account-data"
    exported_at: datetime
    principal: PrincipalExport
    library: LibraryPortabilityExportResponse
    personalization: PersonalizationExportResponse
    operational_records: list[OperationalRecordExport]


class AccountDeleteRequest(BaseModel):
    confirmation: Literal["DELETE"] = Field(
        description="Explicit destructive confirmation required for whole-account erasure."
    )


class AccountDeleteResponse(BaseModel):
    deleted_at: datetime
    principal_deleted: Literal[True] = True
    interaction_events_deleted: int = Field(ge=0)
    private_local_imports_deleted: int = Field(ge=0)
    storage_objects_queued: int = Field(ge=0)
    storage_objects_deleted_immediately: int = Field(ge=0)
    storage_objects_pending_retry: int = Field(ge=0)


__all__ = [
    "AccountDeleteRequest",
    "AccountDeleteResponse",
    "AccountPrivacyExportResponse",
    "OperationalRecordExport",
    "PrincipalExport",
]
