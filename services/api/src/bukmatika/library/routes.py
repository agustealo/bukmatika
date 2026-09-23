from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.domain import (
    CollectionCreate,
    CollectionResponse,
    CollectionUpdate,
    LibraryItemResponse,
    LibraryOrganizationResponse,
    LibraryReadingStatus,
    LibraryResponse,
    TagAssignRequest,
    TagResponse,
    TagUpdate,
    WorkDossierResponse,
)
from bukmatika.library.service import (
    DossierIdentityConflict,
    DossierNotFound,
    LibraryOrganizationConflict,
    LibraryOrganizationNotFound,
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
    reading_status: Annotated[LibraryReadingStatus | None, Query()] = None,
    collection_id: Annotated[UUID | None, Query()] = None,
    tag_id: Annotated[UUID | None, Query()] = None,
) -> LibraryResponse:
    try:
        return await service.list_library(
            principal_id=identity.principal_id,
            reading_status=reading_status,
            collection_id=collection_id,
            tag_id=tag_id,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library organization filter not found",
        ) from exc


@router.get("/library/organization", response_model=LibraryOrganizationResponse)
async def library_organization(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> LibraryOrganizationResponse:
    return await service.organization(principal_id=identity.principal_id)


@router.post(
    "/library/collections",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    create: CollectionCreate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> CollectionResponse:
    return await service.create_collection(principal_id=identity.principal_id, create=create)


@router.post("/library/collections/{collection_id}", response_model=CollectionResponse)
async def update_collection(
    collection_id: UUID,
    update: CollectionUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> CollectionResponse:
    try:
        return await service.update_collection(
            principal_id=identity.principal_id,
            collection_id=collection_id,
            update=update,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found",
        ) from exc
    except LibraryOrganizationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "LIBRARY_COLLECTION_NAME_CONFLICT"},
        ) from exc


@router.post(
    "/library/collections/{collection_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_collection(
    collection_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> Response:
    try:
        await service.delete_collection(
            principal_id=identity.principal_id,
            collection_id=collection_id,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/library/collections/{collection_id}/entries/{library_entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def add_collection_entry(
    collection_id: UUID,
    library_entry_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> Response:
    try:
        await service.add_collection_entry(
            principal_id=identity.principal_id,
            collection_id=collection_id,
            library_entry_id=library_entry_id,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection or library entry not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/library/collections/{collection_id}/entries/{library_entry_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_collection_entry(
    collection_id: UUID,
    library_entry_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> Response:
    try:
        await service.remove_collection_entry(
            principal_id=identity.principal_id,
            collection_id=collection_id,
            library_entry_id=library_entry_id,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collection or library entry not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/library/entries/{library_entry_id}/tags", response_model=TagResponse)
async def assign_tag(
    library_entry_id: UUID,
    request: TagAssignRequest,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> TagResponse:
    try:
        return await service.assign_tag(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            request=request,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Library entry not found",
        ) from exc


@router.post("/library/tags/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: UUID,
    update: TagUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> TagResponse:
    try:
        return await service.update_tag(
            principal_id=identity.principal_id,
            tag_id=tag_id,
            update=update,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from exc
    except LibraryOrganizationConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "LIBRARY_TAG_NAME_CONFLICT"},
        ) from exc


@router.post(
    "/library/entries/{library_entry_id}/tags/{tag_id}/remove",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_entry_tag(
    library_entry_id: UUID,
    tag_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> Response:
    try:
        await service.remove_entry_tag(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            tag_id=tag_id,
        )
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag or library entry not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/library/tags/{tag_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> Response:
    try:
        await service.delete_tag(principal_id=identity.principal_id, tag_id=tag_id)
    except LibraryOrganizationNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/library/works/{work_id}", response_model=LibraryItemResponse)
async def save_work(
    work_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryService, Depends(library_service)],
) -> LibraryItemResponse:
    try:
        return await service.save_work(principal_id=identity.principal_id, work_id=work_id)
    except LibraryTargetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work not found",
        ) from exc


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Edition not found",
        ) from exc


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work not found",
        ) from exc


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
