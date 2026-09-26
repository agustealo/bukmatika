from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.privacy.domain import (
    AccountDeleteRequest,
    AccountDeleteResponse,
    AccountPrivacyExportResponse,
)
from bukmatika.privacy.service import AccountPrivacyService, PrincipalNotFound

settings = get_settings()
router = APIRouter(prefix="/v1/privacy", tags=["privacy"])


def privacy_service(request: Request) -> AccountPrivacyService:
    service = request.app.state.account_privacy
    if not isinstance(service, AccountPrivacyService):
        raise RuntimeError("Account privacy service is not initialized")
    return service


@router.get("/export", response_model=AccountPrivacyExportResponse)
async def export_account_data(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[AccountPrivacyService, Depends(privacy_service)],
) -> AccountPrivacyExportResponse:
    try:
        return await service.export(principal_id=identity.principal_id)
    except PrincipalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRINCIPAL_NOT_FOUND"},
        ) from exc


@router.post("/delete", response_model=AccountDeleteResponse)
async def delete_account_data(
    deletion: AccountDeleteRequest,
    response: Response,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[AccountPrivacyService, Depends(privacy_service)],
) -> AccountDeleteResponse:
    del deletion
    try:
        result = await service.delete_account(principal_id=identity.principal_id)
    except PrincipalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRINCIPAL_NOT_FOUND"},
        ) from exc
    response.delete_cookie(
        key=settings.local_session_cookie_name,
        path="/",
        secure=settings.local_session_secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return result


__all__ = ["privacy_service", "router"]
