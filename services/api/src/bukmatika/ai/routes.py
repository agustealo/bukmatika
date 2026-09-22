from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from bukmatika.ai.execution import (
    AIExecutionDisabled,
    ActionExecutionDenied,
    CapabilityArgumentsInvalid,
)
from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelProviderUnconfigured,
)
from bukmatika.ai.research_domain import GroundedResearchExecutionResponse
from bukmatika.ai.research_service import GroundedResearchSynthesisService
from bukmatika.ai.service import AIDisabled
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence.research import ResearchReaderPositionInvalid, ResearchSelectionDenied
from bukmatika.research import ResearchEvidenceBundleRequest, ResearchEvidenceReferenceInvalid

router = APIRouter(prefix="/v1/ai", tags=["ai"])


class AIProviderStatusResponse(BaseModel):
    configured: bool
    provider: str | None
    model: str | None
    routing: str | None


def model_gateway(request: Request) -> ModelGateway:
    gateway = request.app.state.model_gateway
    return gateway


def grounded_research_service(
    gateway: Annotated[ModelGateway, Depends(model_gateway)],
) -> GroundedResearchSynthesisService:
    return GroundedResearchSynthesisService(gateway=gateway)


@router.get("/status", response_model=AIProviderStatusResponse)
async def ai_provider_status(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    gateway: Annotated[ModelGateway, Depends(model_gateway)],
) -> AIProviderStatusResponse:
    del identity
    provider = gateway.identity
    return AIProviderStatusResponse(
        configured=provider is not None,
        provider=provider.provider if provider is not None else None,
        model=provider.model if provider is not None else None,
        routing=provider.routing if provider is not None else None,
    )


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
    except (AIDisabled, AIExecutionDisabled) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc
    except ResearchSelectionDenied as exc:
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
