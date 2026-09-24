from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, JsonValue

from bukmatika.ai.delegation_domain import DelegationStatus
from bukmatika.ai.delegation_result_domain import DelegationRecentResult


class DelegationConsentAction(StrEnum):
    GRANT = "grant"
    REVOKE = "revoke"


class DelegationConsentScope(StrEnum):
    READ_ONLY = "read_only"


class DelegationConsentRequest(BaseModel):
    action: DelegationConsentAction


class DelegationReviewLibraryEntry(BaseModel):
    library_entry_id: UUID
    title: str


class DelegationReviewStep(BaseModel):
    step_id: str
    capability: str
    arguments: dict[str, JsonValue]
    rationale: str


class ActiveDelegationControlItem(BaseModel):
    delegation_id: UUID
    plan_id: UUID
    status: DelegationStatus
    review_valid: bool
    user_request: str | None = None
    library_entries: list[DelegationReviewLibraryEntry]
    steps: list[DelegationReviewStep]
    step_ids: list[str]
    current_step_index: int
    remaining_steps: int
    attempts_used: int
    max_retries_per_step: int
    max_total_attempts: int
    remaining_attempts: int
    max_runtime_seconds: int
    remaining_runtime_seconds: int
    started_at: datetime | None = None
    stop_requested_at: datetime | None = None


class DelegationControlStatusResponse(BaseModel):
    ai_enabled: bool
    autonomy_level: int
    level2_enabled: bool
    consent_scope: DelegationConsentScope | None = None
    consent_policy_version: str | None = None
    consented_at: datetime | None = None
    revoked_at: datetime | None = None
    active_delegations: list[ActiveDelegationControlItem]
    recent_results: list[DelegationRecentResult]


class DelegationConsentUnavailable(RuntimeError):
    code = "DELEGATION_CONSENT_UNAVAILABLE"


class DelegationConsentConflict(RuntimeError):
    code = "DELEGATION_CONSENT_CONFLICT"
