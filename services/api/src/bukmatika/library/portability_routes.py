from typing import Annotated

from fastapi import APIRouter, Depends

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.library.portability_domain import LibraryPortabilityExportResponse

router = APIRouter(prefix="/v1/library", tags=["library-portability"])
_portability_service = LibraryPortabilityService()


def portability_service() -> LibraryPortabilityService:
    return _portability_service


@router.get("/export", response_model=LibraryPortabilityExportResponse)
async def export_library(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryPortabilityService, Depends(portability_service)],
) -> LibraryPortabilityExportResponse:
    return await service.export(principal_id=identity.principal_id)
