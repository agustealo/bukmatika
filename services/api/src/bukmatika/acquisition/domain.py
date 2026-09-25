from datetime import datetime
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
    CANCELLED = "cancelled"


class AcquisitionApprovalMode(StrEnum):
    ALWAYS_ASK = "always_ask"
    AUTO_ELIGIBLE = "auto_eligible"


class AcquisitionRequestStatus(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    ACTIVE = "active"
    STORED = "stored"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


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


class AcquisitionJobResponse(BaseModel):
    acquisition_id: UUID
    asset_id: UUID
    job_id: UUID | None = None
    acquisition_status: AcquisitionStatus
    job_status: str


class AcquisitionPolicyUpdate(BaseModel):
    approval_mode: AcquisitionApprovalMode


class AcquisitionPolicyResponse(BaseModel):
    approval_mode: AcquisitionApprovalMode


class AcquisitionRequestResponse(BaseModel):
    request_id: UUID
    asset_id: UUID
    acquisition_id: UUID | None
    approval_mode: AcquisitionApprovalMode
    status: AcquisitionRequestStatus
    approved_at: datetime | None
    cancelled_at: datetime | None
    acquisition_status: AcquisitionStatus | None
