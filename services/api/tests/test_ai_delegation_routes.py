from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationConflict,
    DelegationExecutionDisabled,
    DelegationInvalid,
    DelegationNotFound,
    DelegationProposalRequest,
    DelegationStepUnavailable,
)
from bukmatika.ai.routes import (
    decide_delegation,
    delegation_status,
    propose_delegation,
    stop_delegation,
)
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.main import app


class _FailingDelegationService:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def propose(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise self._error

    async def get(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise self._error

    async def decide(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise self._error

    async def request_stop(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise self._error


def _identity() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=uuid4(),
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _proposal() -> DelegationProposalRequest:
    return DelegationProposalRequest(
        step_ids=["research"],
        max_runtime_seconds=300,
        max_retries_per_step=0,
        max_total_attempts=1,
    )


def test_delegation_public_routes_are_mounted_without_runtime_execution_surface() -> None:
    paths = set(app.openapi()["paths"])
    assert "/v1/ai/plans/{plan_id}/delegations" in paths
    assert "/v1/ai/delegations/{delegation_id}" in paths
    assert "/v1/ai/delegations/{delegation_id}/approval" in paths
    assert "/v1/ai/delegations/{delegation_id}/stop" in paths

    assert "/v1/ai/delegations/{delegation_id}/start" not in paths
    assert "/v1/ai/delegations/{delegation_id}/activate" not in paths
    assert "/v1/ai/delegations/{delegation_id}/attempts" not in paths
    assert "/v1/ai/delegations/{delegation_id}/complete" not in paths


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (DelegationNotFound("missing"), 404, "DELEGATION_NOT_FOUND"),
        (DelegationInvalid("invalid"), 422, "DELEGATION_INVALID"),
        (
            DelegationExecutionDisabled("disabled"),
            409,
            "DELEGATION_EXECUTION_DISABLED",
        ),
        (DelegationStepUnavailable("blocked"), 409, "DELEGATION_STEP_UNAVAILABLE"),
    ],
)
async def test_propose_delegation_maps_control_errors(
    error: Exception,
    expected_status: int,
    expected_code: str,
) -> None:
    with pytest.raises(HTTPException) as captured:
        await propose_delegation(
            plan_id=uuid4(),
            proposal=_proposal(),
            identity=_identity(),
            service=_FailingDelegationService(error),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == expected_status
    assert captured.value.detail == {"code": expected_code}


async def test_delegation_status_hides_unavailable_principal_state_as_404() -> None:
    with pytest.raises(HTTPException) as captured:
        await delegation_status(
            delegation_id=uuid4(),
            identity=_identity(),
            service=_FailingDelegationService(DelegationNotFound("missing")),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == {"code": "DELEGATION_NOT_FOUND"}


async def test_delegation_approval_conflict_maps_to_409() -> None:
    with pytest.raises(HTTPException) as captured:
        await decide_delegation(
            delegation_id=uuid4(),
            decision=DelegationApprovalRequest(
                decision=DelegationApprovalDecision.APPROVED,
            ),
            identity=_identity(),
            service=_FailingDelegationService(DelegationConflict("final")),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == {"code": "DELEGATION_CONFLICT"}


async def test_delegation_stop_conflict_maps_to_409() -> None:
    with pytest.raises(HTTPException) as captured:
        await stop_delegation(
            delegation_id=uuid4(),
            identity=_identity(),
            service=_FailingDelegationService(DelegationConflict("terminal")),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == {"code": "DELEGATION_CONFLICT"}
