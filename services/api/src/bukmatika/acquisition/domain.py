from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class AcquisitionStatus(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    STORED = "stored"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class AcquisitionResponse(BaseModel):
    acquisition_id: UUID
    asset_id: UUID
    status: AcquisitionStatus
    rights_state: str | None = None
    sha256: str | None = None
    byte_size: int | None = None
    media_type: str | None = None
    storage_key: str | None = None
    error_code: str | None = None
