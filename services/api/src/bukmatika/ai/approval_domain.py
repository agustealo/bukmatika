import hashlib
import json
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, JsonValue

from bukmatika.ai.domain import CapabilityName, PlanStep


class UserApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ActionApprovalRequest(BaseModel):
    decision: UserApprovalDecision


class ActionApprovalResponse(BaseModel):
    approval_id: UUID
    principal_id: UUID
    plan_id: UUID
    action_decision_id: UUID
    step_id: str
    capability: CapabilityName
    decision: UserApprovalDecision
    step_fingerprint: str
    decided_at: datetime


class PendingActionApproval(BaseModel):
    plan_id: UUID
    action_decision_id: UUID
    step_id: str
    capability: CapabilityName
    arguments: dict[str, JsonValue]
    rationale: str
    user_request: str
    policy_reason: str
    policy_version: str
    step_fingerprint: str
    evaluated_at: datetime
    approval_status: UserApprovalDecision | None = None


class PendingActionApprovalResponse(BaseModel):
    items: list[PendingActionApproval]


class ActionApprovalNotFound(LookupError):
    code = "ACTION_APPROVAL_NOT_FOUND"


class ActionApprovalNotRequired(ValueError):
    code = "ACTION_APPROVAL_NOT_REQUIRED"


class ActionApprovalConflict(RuntimeError):
    code = "ACTION_APPROVAL_CONFLICT"


class ActionApprovalMissing(RuntimeError):
    code = "ACTION_APPROVAL_MISSING"


class ActionApprovalRejected(RuntimeError):
    code = "ACTION_APPROVAL_REJECTED"


class ActionApprovalInvalid(RuntimeError):
    code = "ACTION_APPROVAL_INVALID"


class ActionExecutionReceiptInvalid(ActionApprovalInvalid):
    code = "ACTION_EXECUTION_RECEIPT_INVALID"


def action_step_fingerprint(
    *,
    plan_id: UUID,
    action_decision_id: UUID,
    step: PlanStep,
) -> str:
    payload = {
        "plan_id": str(plan_id),
        "action_decision_id": str(action_decision_id),
        "step": step.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
