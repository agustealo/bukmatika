from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from bukmatika.ai.execution import (
    ActionExecutionDenied,
    AIExecutionDisabled,
    CapabilityArgumentsInvalid,
)
from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderNotReady,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelProviderUnconfigured,
)
from bukmatika.ai.research_domain import (
    GroundedResearchExecutionResponse,
    GroundedResearchSelectionRequest,
)
from bukmatika.ai.research_service import (
    GroundedResearchSynthesisService,
    ResearchSelectionEvidenceUnavailable,
)
from bukmatika.ai.routes import model_gateway
from bukmatika.ai.service import AIDisabled
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence.personalization import ContextSelectionDenied
from bukmatika.persistence.research import ResearchReaderPositionInvalid, ResearchSelectionDenied
from bukmatika.research import ResearchEvidenceReferenceInvalid

router = APIRouter(prefix="/v1/ai", tags=["ai"])


def grounded_research_selection_service(
    gateway: Annotated[ModelGateway, Depends(model_gateway)],
) -> GroundedResearchSynthesisService:
    return GroundedResearchSynthesisService(gateway=gateway)


@router.post(
    "/research/answer-selection",
    response_model=GroundedResearchExecutionResponse,
)
async def grounded_research_selection_answer(
    request: GroundedResearchSelectionRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[
        GroundedResearchSynthesisService,
        Depends(grounded_research_selection_service),
    ],
) -> GroundedResearchExecutionResponse:
    try:
        return await service.answer_selection(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionEvidenceUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code},
        ) from exc
    except ModelProviderUnconfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code},
        ) from exc
    except ModelProviderNotReady as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "state": exc.readiness.state.value},
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
