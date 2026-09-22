from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PersonalizationProfileResponse,
    PersonalizationSettingsUpdate,
    PreferenceClaimResponse,
)
from bukmatika.personalization.service import PersonalizationService, PreferenceClaimNotFound

router = APIRouter(prefix="/v1/personalization", tags=["personalization"])
_personalization_service = PersonalizationService()


def personalization_service() -> PersonalizationService:
    return _personalization_service


@router.get("", response_model=PersonalizationProfileResponse)
async def personalization_profile(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PersonalizationService, Depends(personalization_service)],
) -> PersonalizationProfileResponse:
    return await service.profile(principal_id=identity.principal_id)


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
