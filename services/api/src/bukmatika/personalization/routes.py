from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence.personalization import ContextGoalDenied, ContextSelectionDenied
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.control import PersonalizationControlService
from bukmatika.personalization.control_domain import (
    ActivityLedgerResponse,
    PersonalizationControlCenterResponse,
)
from bukmatika.personalization.domain import (
    ContextManifest,
    ContextRequest,
    ExplicitPreferenceRequest,
    PersonalizationProfileResponse,
    PersonalizationSettingsUpdate,
    PreferenceClaimResponse,
)
from bukmatika.personalization.service import PersonalizationService, PreferenceClaimNotFound

router = APIRouter(prefix="/v1/personalization", tags=["personalization"])
_personalization_service = PersonalizationService()
_context_assembler = ContextAssembler()
_control_service = PersonalizationControlService()


def personalization_service() -> PersonalizationService:
    return _personalization_service


def context_assembler() -> ContextAssembler:
    return _context_assembler


def personalization_control_service() -> PersonalizationControlService:
    return _control_service


@router.get("", response_model=PersonalizationProfileResponse)
async def personalization_profile(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> PersonalizationProfileResponse:
    return await service.profile(principal_id=identity.principal_id)


@router.get("/control-center", response_model=PersonalizationControlCenterResponse)
async def personalization_control_center(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[
        PersonalizationControlService,
        Depends(personalization_control_service),
    ],
) -> PersonalizationControlCenterResponse:
    return await service.snapshot(principal_id=identity.principal_id)


@router.get("/activity", response_model=ActivityLedgerResponse)
async def personalization_activity(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[
        PersonalizationControlService,
        Depends(personalization_control_service),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ActivityLedgerResponse:
    return await service.activity(
        principal_id=identity.principal_id,
        limit=limit,
    )


@router.post("/preferences", response_model=PreferenceClaimResponse)
async def set_explicit_preference(
    request: ExplicitPreferenceRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> PreferenceClaimResponse:
    return await service.set_explicit_preference(
        principal_id=identity.principal_id,
        request=request,
    )


@router.post("/preferences/{claim_id}/forget", status_code=status.HTTP_204_NO_CONTENT)
async def forget_preference(
    claim_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> Response:
    try:
        await service.forget_preference(
            principal_id=identity.principal_id,
            claim_id=claim_id,
        )
    except PreferenceClaimNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Active preference claim not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/settings", response_model=PersonalizationProfileResponse)
async def update_personalization_settings(
    update: PersonalizationSettingsUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> PersonalizationProfileResponse:
    return await service.update_settings(
        principal_id=identity.principal_id,
        update=update,
    )


@router.post("/context", response_model=ContextManifest)
async def assemble_context(
    request: ContextRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    assembler: Annotated[ContextAssembler, Depends(context_assembler)],
) -> ContextManifest:
    try:
        return await assembler.assemble(
            principal_id=identity.principal_id,
            request=request,
        )
    except ContextGoalDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEXT_GOAL_UNAVAILABLE"},
        ) from exc
    except ContextSelectionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONTEXT_SELECTION_UNAVAILABLE"},
        ) from exc
