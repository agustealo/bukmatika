from typing import Annotated

from fastapi import APIRouter, Depends

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.domain import LibraryStatusResponse
from bukmatika.library.status import LibraryStatusService

router = APIRouter(prefix="/v1/library", tags=["library-status"])
_status_service = LibraryStatusService()


def library_status_service() -> LibraryStatusService:
    return _status_service


@router.get("/status", response_model=LibraryStatusResponse)
async def library_status(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryStatusService, Depends(library_status_service)],
) -> LibraryStatusResponse:
    return await service.snapshot(principal_id=identity.principal_id)
