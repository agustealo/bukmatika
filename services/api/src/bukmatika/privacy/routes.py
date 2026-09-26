from typing import Annotated

from fastapi import APIRouter, Depends

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.privacy.domain import PrincipalDataExportResponse
from bukmatika.privacy.service import PrincipalDataExportService

router = APIRouter(prefix="/v1/privacy", tags=["privacy"])
_export_service = PrincipalDataExportService()


def principal_data_export_service() -> PrincipalDataExportService:
    return _export_service


@router.get("/export", response_model=PrincipalDataExportResponse)
async def export_principal_data(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[
        PrincipalDataExportService,
        Depends(principal_data_export_service),
    ],
) -> PrincipalDataExportResponse:
    return await service.export(principal_id=identity.principal_id)


__all__ = ["router"]
