from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from bukmatika.ai.gateway import (
    ModelProviderError,
    ModelProviderUnconfigured,
    ModelStructuredOutputError,
)
from bukmatika.ai.service import AIContextUnavailable, AIDisabled
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.research.domain import (
    GroundedAnswerResponse,
    GroundedResearchRequest,
    ResearchSearchRequest,
    ResearchSearchResponse,
)
from bukmatika.research.grounded import GroundedCitationInvalid, GroundedResearchService
from bukmatika.research.service import ResearchSelectionDenied, ResearchService

router = APIRouter(prefix="/v1/research", tags=["research"])
_research_service = ResearchService()
_grounded_research_service = GroundedResearchService(research_service=_research_service)


def research_service() -> ResearchService:
    return _research_service


def grounded_research_service() -> GroundedResearchService:
    return _grounded_research_service


@router.post("/search", response_model=ResearchSearchResponse)
async def research_search(
    request: ResearchSearchRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ResearchService, Depends(research_service)],
) -> ResearchSearchResponse:
    try:
        return await service.search(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more selected library entries are unavailable",
        ) from exc


@router.post("/answer", response_model=GroundedAnswerResponse)
async def grounded_research_answer(
    request: GroundedResearchRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[GroundedResearchService, Depends(grounded_research_service)],
) -> GroundedAnswerResponse:
    try:
        return await service.answer(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESEARCH_SELECTION_UNAVAILABLE"},
        ) from exc
    except AIDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc
    except AIContextUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code},
        ) from exc
    except ModelProviderUnconfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code},
        ) from exc
    except (ModelProviderError, ModelStructuredOutputError, GroundedCitationInvalid) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": exc.code},
        ) from exc
