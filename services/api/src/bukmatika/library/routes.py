from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.domain import LibraryItemResponse, LibraryResponse, WorkDossierResponse
from bukmatika.library.service import (
    DossierIdentityConflict,
    DossierNotFound,
    LibraryService,
    LibraryTargetNotFound,
)

router = APIRouter(prefix="/v1", tags=["library"])
_library_service = LibraryService()


def library_service() -> LibraryService:
    return _library_service


@router.get("/library", response_model=LibraryResponse)
async def list_library(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> LibraryResponse:
    return await service.list_library(principal_id=identity.principal_id)


@router.post("/library/works/{work_id}", response_model=LibraryItemResponse)
async def save_work(
    work_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> LibraryItemResponse:
    try:
        return await service.save_work(principal_id=identity.principal_id, work_id=work_id)
    except LibraryTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found") from exc


@router.post("/library/editions/{edition_id}", response_model=LibraryItemResponse)
async def save_edition(
    edition_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> LibraryItemResponse:
    try:
        return await service.save_edition(
            principal_id=identity.principal_id,
            edition_id=edition_id,
        )
    except LibraryTargetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edition not found") from exc


@router.get("/dossiers/works/{work_id}", response_model=WorkDossierResponse)
async def work_dossier(
    work_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> WorkDossierResponse:
    try:
        return await service.dossier_for_work(
            principal_id=identity.principal_id,
            work_id=work_id,
        )
    except DossierNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found") from exc


@router.get("/dossiers/source", response_model=WorkDossierResponse)
async def source_dossier(
    provider: Annotated[str, Query(min_length=1, max_length=64)],
    record_id: Annotated[str, Query(min_length=1, max_length=2048)],
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> WorkDossierResponse:
    try:
        return await service.dossier_for_source(
            principal_id=identity.principal_id,
            provider=provider,
            provider_record_id=record_id,
        )
    except DossierNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dossier not found",
        ) from exc
    except DossierIdentityConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DOSSIER_IDENTITY_CONFLICT"},
        ) from exc
