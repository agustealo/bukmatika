from enum import StrEnum
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from bukmatika.ai.approval_domain import (
    ActionApprovalConflict,
    ActionApprovalInvalid,
    ActionApprovalNotFound,
    ActionApprovalNotRequired,
    ActionApprovalRejected,
    ActionApprovalRequest,
    ActionApprovalResponse,
    PendingActionApprovalResponse,
)
from bukmatika.ai.approvals import ApprovalService
from bukmatika.ai.configuration import (
    LocalModelConfigurationResponse,
    LocalModelConfigurationUpdate,
    LocalModelInventoryResponse,
    PrincipalModelRuntimeResolver,
)
from bukmatika.ai.execution import (
    ActionApprovalRequired,
    ActionExecutionDenied,
    AIExecutionDisabled,
    CapabilityArgumentsInvalid,
    CapabilityExecutionResponse,
    CapabilityExecutorUnavailable,
    ExecutionCoordinator,
)
from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderNotReady,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelProviderUnconfigured,
    UnconfiguredModelGateway,
)
from bukmatika.ai.research_domain import GroundedResearchExecutionResponse
from bukmatika.ai.research_service import GroundedResearchSynthesisService
from bukmatika.ai.service import AIDisabled
from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence.execution import ActionExecutionNotFound, PlanIntegrityError
from bukmatika.persistence.personalization import ContextSelectionDenied
from bukmatika.persistence.research import ResearchReaderPositionInvalid, ResearchSelectionDenied
from bukmatika.personalization.service import PersonalizationService
from bukmatika.research import ResearchEvidenceBundleRequest, ResearchEvidenceReferenceInvalid

router = APIRouter(prefix="/v1/ai", tags=["ai"])
_personalization_service = PersonalizationService()
_approval_service = ApprovalService()
_execution_coordinator = ExecutionCoordinator()


class AIAvailabilityState(StrEnum):
    AI_DISABLED = "ai_disabled"
    UNCONFIGURED = "unconfigured"
    PROVIDER_UNREACHABLE = "provider_unreachable"
    PROVIDER_INVALID = "provider_invalid"
    MODEL_MISSING = "model_missing"
    READY = "ready"


class AIProviderStatusResponse(BaseModel):
    configured: bool
    ready: bool
    ai_enabled: bool
    state: AIAvailabilityState
    provider: str | None
    model: str | None
    routing: str | None


def model_runtime_resolver(request: Request) -> PrincipalModelRuntimeResolver:
    candidate = getattr(request.app.state, "model_gateway", None)
    installation_gateway: ModelGateway = (
        UnconfiguredModelGateway() if candidate is None else cast(ModelGateway, candidate)
    )
    return PrincipalModelRuntimeResolver(
        settings=get_settings(),
        installation_gateway=installation_gateway,
    )


async def model_gateway(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    resolver: Annotated[PrincipalModelRuntimeResolver, Depends(model_runtime_resolver)],
) -> ModelGateway:
    return (await resolver.resolve(principal_id=identity.principal_id)).gateway


def personalization_service() -> PersonalizationService:
    return _personalization_service


def approval_service() -> ApprovalService:
    return _approval_service


def execution_coordinator() -> ExecutionCoordinator:
    return _execution_coordinator


def grounded_research_service(
    gateway: Annotated[ModelGateway, Depends(model_gateway)],
) -> GroundedResearchSynthesisService:
    return GroundedResearchSynthesisService(gateway=gateway)


@router.get("/configuration", response_model=LocalModelConfigurationResponse)
async def local_model_configuration(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    resolver: Annotated[PrincipalModelRuntimeResolver, Depends(model_runtime_resolver)],
) -> LocalModelConfigurationResponse:
    return await resolver.configuration(principal_id=identity.principal_id)


@router.post("/configuration", response_model=LocalModelConfigurationResponse)
async def update_local_model_configuration(
    update: LocalModelConfigurationUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    resolver: Annotated[PrincipalModelRuntimeResolver, Depends(model_runtime_resolver)],
) -> LocalModelConfigurationResponse:
    return await resolver.update_configuration(
        principal_id=identity.principal_id,
        update=update,
    )


@router.get("/local/models", response_model=LocalModelInventoryResponse)
async def local_model_inventory(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    resolver: Annotated[PrincipalModelRuntimeResolver, Depends(model_runtime_resolver)],
    profile_service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> LocalModelInventoryResponse:
    profile = await profile_service.profile(principal_id=identity.principal_id)
    if not profile.ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "AI_DISABLED"},
        )
    return await resolver.installed_models()


@router.get("/status", response_model=AIProviderStatusResponse)
async def ai_provider_status(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    gateway: Annotated[ModelGateway, Depends(model_gateway)],
    profile_service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> AIProviderStatusResponse:
    profile = await profile_service.profile(principal_id=identity.principal_id)
    provider = gateway.identity
    if not profile.ai_enabled:
        return AIProviderStatusResponse(
            configured=provider is not None,
            ready=False,
            ai_enabled=False,
            state=AIAvailabilityState.AI_DISABLED,
            provider=provider.provider if provider is not None else None,
            model=provider.model if provider is not None else None,
            routing=provider.routing if provider is not None else None,
        )

    readiness = await gateway.readiness()
    provider = readiness.identity
    return AIProviderStatusResponse(
        configured=readiness.configured,
        ready=readiness.ready,
        ai_enabled=True,
        state=AIAvailabilityState(readiness.state.value),
        provider=provider.provider if provider is not None else None,
        model=provider.model if provider is not None else None,
        routing=provider.routing if provider is not None else None,
    )


@router.get("/approvals/pending", response_model=PendingActionApprovalResponse)
async def pending_action_approvals(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ApprovalService, Depends(approval_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PendingActionApprovalResponse:
    return await service.pending(principal_id=identity.principal_id, limit=limit)


@router.post(
    "/actions/{action_decision_id}/approval",
    response_model=ActionApprovalResponse,
)
async def decide_action_approval(
    action_decision_id: UUID,
    approval: ActionApprovalRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ApprovalService, Depends(approval_service)],
) -> ActionApprovalResponse:
    try:
        return await service.decide(
            principal_id=identity.principal_id,
            action_decision_id=action_decision_id,
            request=approval,
        )
    except ActionApprovalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code},
        ) from exc
    except (ActionApprovalNotRequired, ActionApprovalConflict) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc


@router.post(
    "/plans/{plan_id}/steps/{step_id}/execute",
    response_model=CapabilityExecutionResponse,
)
async def execute_planned_action(
    plan_id: UUID,
    step_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    coordinator: Annotated[ExecutionCoordinator, Depends(execution_coordinator)],
) -> CapabilityExecutionResponse:
    try:
        return await coordinator.execute(
            principal_id=identity.principal_id,
            plan_id=plan_id,
            step_id=step_id,
        )
    except ActionExecutionNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code},
        ) from exc
    except CapabilityArgumentsInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code},
        ) from exc
    except (
        AIExecutionDisabled,
        ActionApprovalRequired,
        ActionApprovalRejected,
        ActionApprovalInvalid,
        ActionExecutionDenied,
        CapabilityExecutorUnavailable,
        PlanIntegrityError,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": getattr(exc, "code", "PLAN_INTEGRITY_ERROR")},
        ) from exc


@router.post("/research/answer", response_model=GroundedResearchExecutionResponse)
async def grounded_research_answer(
    request: ResearchEvidenceBundleRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[GroundedResearchSynthesisService, Depends(grounded_research_service)],
) -> GroundedResearchExecutionResponse:
    try:
        return await service.answer(
            principal_id=identity.principal_id,
            request=request,
        )
    except ModelProviderUnconfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code},
        ) from exc
    except ModelProviderNotReady as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": exc.code,
                "state": exc.readiness.state.value,
            },
        ) from exc
    except (AIDisabled, AIExecutionDisabled) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc
    except (ContextSelectionDenied, ResearchSelectionDenied) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESEARCH_SELECTION_UNAVAILABLE"},
        ) from exc
    except (ResearchReaderPositionInvalid, CapabilityArgumentsInvalid) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": getattr(exc, "code", "RESEARCH_READER_POSITION_INVALID")},
        ) from exc
    except ActionExecutionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc
    except (
        ModelProviderRequestFailed,
        ModelProviderResponseInvalid,
        ResearchEvidenceReferenceInvalid,
    ) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code},
        ) from exc
