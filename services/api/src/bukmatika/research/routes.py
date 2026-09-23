from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.research.domain import (
    ResearchCompareRequest,
    ResearchCompareResponse,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceBundleResponse,
    ResearchMentionsResponse,
    ResearchSearchRequest,
    ResearchSearchResponse,
    ResearchTimelineResponse,
)
from bukmatika.research.service import (
    ResearchReaderPositionInvalid,
    ResearchSelectionDenied,
    ResearchService,
)

router = APIRouter(prefix="/v1/research", tags=["research"])
_research_service = ResearchService()


def research_service() -> ResearchService:
    return _research_service


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


@router.post("/compare", response_model=ResearchCompareResponse)
async def research_compare(
    request: ResearchCompareRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ResearchService, Depends(research_service)],
) -> ResearchCompareResponse:
    try:
        return await service.compare(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more comparison sources are unavailable",
        ) from exc


@router.post("/evidence", response_model=ResearchEvidenceBundleResponse)
async def research_evidence(
    request: ResearchEvidenceBundleRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ResearchService, Depends(research_service)],
) -> ResearchEvidenceBundleResponse:
    try:
        return await service.evidence_bundle(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader or research selection is unavailable",
        ) from exc
    except ResearchReaderPositionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.post("/timeline", response_model=ResearchTimelineResponse)
async def research_timeline(
    request: ResearchEvidenceBundleRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ResearchService, Depends(research_service)],
) -> ResearchTimelineResponse:
    try:
        return await service.timeline(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader or research selection is unavailable",
        ) from exc
    except ResearchReaderPositionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.post("/mentions", response_model=ResearchMentionsResponse)
async def research_mentions(
    request: ResearchEvidenceBundleRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ResearchService, Depends(research_service)],
) -> ResearchMentionsResponse:
    try:
        return await service.mentions(
            principal_id=identity.principal_id,
            request=request,
        )
    except ResearchSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader or research selection is unavailable",
        ) from exc
    except ResearchReaderPositionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
