from typing import Annotated

from fastapi import APIRouter, Depends

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.readiness import LibraryReadinessResponse, LibraryReadinessService

router = APIRouter(prefix="/v1", tags=["library"])
_readiness_service = LibraryReadinessService()


def readiness_service() -> LibraryReadinessService:
    return _readiness_service


@router.get("/library/readiness", response_model=LibraryReadinessResponse)
async def library_readiness(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryReadinessService, Depends(readiness_service)],
) -> LibraryReadinessResponse:
    return await service.readiness(principal_id=identity.principal_id)
