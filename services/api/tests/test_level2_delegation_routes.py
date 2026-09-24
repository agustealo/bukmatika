from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from bukmatika.ai.delegation_control_domain import (
    DelegationConsentAction,
    DelegationConsentRequest,
    DelegationConsentUnavailable,
    DelegationControlStatusResponse,
)
from bukmatika.ai.delegation_domain import DelegationNotFound, DelegationStatus
from bukmatika.ai.delegation_result_domain import DelegationRecentResult
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.personalization.routes import (
    decide_delegation_consent,
    delegation_recent_results,
    start_delegation,
)


def _identity() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=uuid4(),
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


class _ConsentCaptureService:
    def __init__(self) -> None:
        self.principal_id: UUID | None = None
        self.request: DelegationConsentRequest | None = None

    async def decide_consent(
        self,
        *,
        principal_id: UUID,
        request: DelegationConsentRequest,
    ) -> DelegationControlStatusResponse:
        self.principal_id = principal_id
        self.request = request
        return DelegationControlStatusResponse(
            ai_enabled=True,
            autonomy_level=2,
            level2_enabled=True,
            active_delegations=[],
        )


class _OutcomeCaptureService:
    def __init__(self) -> None:
        self.principal_id: UUID | None = None
        self.delegation_id = uuid4()
        self.outcome_at = datetime.now(UTC)

    async def recent(self, *, principal_id: UUID) -> list[DelegationRecentResult]:
        self.principal_id = principal_id
        return [
            DelegationRecentResult(
                delegation_id=self.delegation_id,
                status=DelegationStatus.FAILED,
                outcome_at=self.outcome_at,
                completed_at=self.outcome_at,
                failure_code="DELEGATED_SEARCH_FAILED",
                available=False,
            )
        ]


class _ConsentUnavailableService:
    async def decide_consent(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise DelegationConsentUnavailable("Level 2 consent cannot be granted")


class _DelegationMissingService:
    async def start(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise DelegationNotFound("Delegation is unavailable")


async def test_consent_route_forwards_exact_authenticated_principal() -> None:
    identity = _identity()
    service = _ConsentCaptureService()
    request = DelegationConsentRequest(action=DelegationConsentAction.GRANT)

    response = await decide_delegation_consent(
        request=request,
        identity=identity,
        service=service,  # type: ignore[arg-type]
    )

    assert service.principal_id == identity.principal_id
    assert service.request == request
    assert response.level2_enabled is True
    assert response.autonomy_level == 2


async def test_delegation_outcome_route_forwards_authenticated_principal() -> None:
    identity = _identity()
    service = _OutcomeCaptureService()

    response = await delegation_recent_results(
        identity=identity,
        service=service,  # type: ignore[arg-type]
    )

    assert service.principal_id == identity.principal_id
    assert len(response) == 1
    assert response[0].delegation_id == service.delegation_id
    assert response[0].status is DelegationStatus.FAILED
    assert response[0].failure_code == "DELEGATED_SEARCH_FAILED"


async def test_consent_route_maps_unavailable_to_conflict() -> None:
    with pytest.raises(HTTPException) as captured:
        await decide_delegation_consent(
            request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
            identity=_identity(),
            service=_ConsentUnavailableService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 409
    assert captured.value.detail == {"code": "DELEGATION_CONSENT_UNAVAILABLE"}


async def test_start_route_maps_principal_scoped_missing_delegation_to_not_found() -> None:
    with pytest.raises(HTTPException) as captured:
        await start_delegation(
            delegation_id=uuid4(),
            identity=_identity(),
            service=_DelegationMissingService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == {"code": "DELEGATION_NOT_FOUND"}
