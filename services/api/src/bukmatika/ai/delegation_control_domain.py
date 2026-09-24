from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from bukmatika.ai.delegation_domain import DelegationStatus


class DelegationConsentAction(StrEnum):
    GRANT = "grant"
    REVOKE = "revoke"


class DelegationConsentScope(StrEnum):
    READ_ONLY = "read_only"


class DelegationConsentRequest(BaseModel):
    action: DelegationConsentAction


class ActiveDelegationControlItem(BaseModel):
    delegation_id: UUID
    plan_id: UUID
    status: DelegationStatus
    step_ids: list[str]
    current_step_index: int
    remaining_steps: int
    attempts_used: int
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


class DelegationConsentUnavailable(RuntimeError):
    code = "DELEGATION_CONSENT_UNAVAILABLE"


class DelegationConsentConflict(RuntimeError):
    code = "DELEGATION_CONSENT_CONFLICT"
