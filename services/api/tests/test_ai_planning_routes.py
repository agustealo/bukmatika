from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from bukmatika.ai.capabilities import PlanCapabilityUnavailable
from bukmatika.ai.domain import (
    CapabilityName,
    PersistedPlanResponse,
    PlannedActionDecision,
    PlanStep,
)
from bukmatika.ai.gateway import ModelProviderUnconfigured
from bukmatika.ai.routes import PlanningRequest, propose_plan
from bukmatika.ai.service import AIDisabled
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.personalization import ContextSelectionDenied
from bukmatika.personalization.domain import ContextManifest, ContextRequest, ContextTask


def _identity() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        principal_id=uuid4(),
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _request() -> PlanningRequest:
    return PlanningRequest(
        user_request="Search my selected books for navigation evidence.",
        context_request=ContextRequest(
            task=ContextTask.RESEARCH,
            library_entry_ids=[uuid4()],
        ),
    )


def _result(request: PlanningRequest) -> PersistedPlanResponse:
    step = PlanStep(
        step_id="search-navigation",
        capability=CapabilityName.RESEARCH_SEARCH,
        arguments={
            "query": "navigation",
            "library_entry_ids": [
                str(value) for value in request.context_request.library_entry_ids
            ],
            "limit": 12,
        },
        rationale="Find grounded evidence in the explicitly selected books.",
    )
    return PersistedPlanResponse(
        plan_id=uuid4(),
        status="proposed",
        summary="Search the selected books for grounded navigation evidence.",
        context=ContextManifest(
            task=ContextTask.RESEARCH,
            ai_enabled=True,
            learning_enabled=True,
            autonomy_level=2,
            model_context_ready=True,
            preferences=[],
            goal=None,
            library_entries=[],
            available_capabilities=[CapabilityName.RESEARCH_SEARCH.value],
            exclusion_reasons=[],
        ),
        steps=[step],
        decisions=[
            PlannedActionDecision(
                step_id=step.step_id,
                capability=step.capability,
                decision="allow",
                reason="Read-only capability is allowed.",
                policy_version="ai-action-policy-v1",
            )
        ],
    )


class _SuccessPlanningService:
    def __init__(self, result: PersistedPlanResponse) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def propose(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return self.result


class _FailurePlanningService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def propose(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise self.error


async def test_planning_route_preserves_principal_and_explicit_context() -> None:
    identity = _identity()
    request = _request()
    expected = _result(request)
    service = _SuccessPlanningService(expected)

    result = await propose_plan(
        request=request,
        identity=identity,
        service=service,  # type: ignore[arg-type]
    )

    assert result == expected
    assert service.calls == [
        {
            "principal_id": identity.principal_id,
            "user_request": request.user_request,
            "context_request": request.context_request,
        }
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            ModelProviderUnconfigured("No model provider is configured"),
            503,
            {"code": "MODEL_PROVIDER_UNCONFIGURED"},
        ),
        (
            AIDisabled("AI is disabled"),
            409,
            {"code": "AI_DISABLED"},
        ),
        (
            ContextSelectionDenied("Selected library entry is unavailable"),
            404,
            {"code": "AI_CONTEXT_SELECTION_UNAVAILABLE"},
        ),
        (
            PlanCapabilityUnavailable("Capability is unavailable"),
            422,
            {"code": "PLAN_CAPABILITY_UNAVAILABLE"},
        ),
    ],
)
async def test_planning_route_maps_fail_closed_errors(
    error: Exception,
    status_code: int,
    detail: dict[str, str],
) -> None:
    with pytest.raises(HTTPException) as captured:
        await propose_plan(
            request=_request(),
            identity=_identity(),
            service=_FailurePlanningService(error),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == status_code
    assert captured.value.detail == detail
