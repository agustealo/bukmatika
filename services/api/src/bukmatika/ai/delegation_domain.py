from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class DelegationStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    RUNNING = "running"
    STOP_REQUESTED = "stop_requested"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DelegationApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class DelegationAttemptStatus(StrEnum):
    AUTHORIZED = "authorized"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DelegationProposalRequest(BaseModel):
    step_ids: list[str] = Field(min_length=1, max_length=12)
    max_runtime_seconds: int = Field(ge=30, le=3_600)
    max_retries_per_step: int = Field(default=0, ge=0, le=3)
    max_total_attempts: int = Field(ge=1, le=48)

    @field_validator("step_ids")
    @classmethod
    def validate_step_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 64 for value in normalized):
            raise ValueError("Delegation step IDs must contain between 1 and 64 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Delegation step IDs must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_attempt_budget(self) -> "DelegationProposalRequest":
        minimum = len(self.step_ids)
        maximum = len(self.step_ids) * (self.max_retries_per_step + 1)
        if self.max_total_attempts < minimum or self.max_total_attempts > maximum:
            raise ValueError(
                "Delegation total attempt budget must cover each step once and cannot exceed "
                "the selected-step retry ceiling"
            )
        return self


class DelegationApprovalRequest(BaseModel):
    decision: DelegationApprovalDecision


class DelegationResponse(BaseModel):
    delegation_id: UUID
    principal_id: UUID
    plan_id: UUID
    status: DelegationStatus
    step_ids: list[str]
    plan_fingerprint: str
    delegation_fingerprint: str
    max_runtime_seconds: int
    max_retries_per_step: int
    max_total_attempts: int
    attempts_used: int
    current_step_index: int
    approval_decision: DelegationApprovalDecision | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    stop_requested_at: datetime | None = None
    stopped_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = None


class DelegationAttemptPermit(BaseModel):
    attempt_id: UUID
    delegation_id: UUID
    plan_id: UUID
    step_id: str
    attempt_number: int = Field(ge=1)
    delegation_fingerprint: str
    authorized_at: datetime


class DelegationAttemptClaim(BaseModel):
    permit: DelegationAttemptPermit
    claim_token: UUID
    claimed_at: datetime
    claim_expires_at: datetime


class DelegationAttemptCompletion(BaseModel):
    succeeded: bool
    error_code: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_error_code(self) -> "DelegationAttemptCompletion":
        if self.succeeded and self.error_code is not None:
            raise ValueError("Successful delegated attempts cannot include an error code")
        if not self.succeeded and not self.error_code:
            raise ValueError("Failed delegated attempts require an error code")
        return self


class DelegationNotFound(LookupError):
    code = "DELEGATION_NOT_FOUND"


class DelegationInvalid(ValueError):
    code = "DELEGATION_INVALID"


class DelegationConflict(RuntimeError):
    code = "DELEGATION_CONFLICT"


class DelegationApprovalRequired(RuntimeError):
    code = "DELEGATION_APPROVAL_REQUIRED"


class DelegationExecutionDisabled(RuntimeError):
    code = "DELEGATION_EXECUTION_DISABLED"


class DelegationBudgetExceeded(RuntimeError):
    code = "DELEGATION_BUDGET_EXCEEDED"


class DelegationStopRequested(RuntimeError):
    code = "DELEGATION_STOP_REQUESTED"


class DelegationStepUnavailable(RuntimeError):
    code = "DELEGATION_STEP_UNAVAILABLE"


class DelegationAttemptClaimExpired(RuntimeError):
    code = "DELEGATION_ATTEMPT_CLAIM_EXPIRED"
