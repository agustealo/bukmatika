from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.provenance import MetadataProvenanceService
from bukmatika.library.provenance_domain import WorkMetadataProvenanceResponse
from bukmatika.persistence.library import DossierIdentityConflict, DossierNotFound

router = APIRouter(prefix="/v1/dossiers", tags=["library"])
_provenance_service = MetadataProvenanceService()


def metadata_provenance_service() -> MetadataProvenanceService:
    return _provenance_service


@router.get(
    "/works/{work_id}/metadata-provenance",
    response_model=WorkMetadataProvenanceResponse,
)
async def metadata_provenance_for_work(
    work_id: UUID,
    _identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[MetadataProvenanceService, Depends(metadata_provenance_service)],
) -> WorkMetadataProvenanceResponse:
    try:
        return await service.dossier_for_work(work_id=work_id)
    except DossierNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Dossier not found",
        ) from exc


@router.get(
    "/source/metadata-provenance",
    response_model=WorkMetadataProvenanceResponse,
)
async def metadata_provenance_for_source(
    _identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[MetadataProvenanceService, Depends(metadata_provenance_service)],
    provider: Annotated[str, Query(min_length=1, max_length=64)],
    record_id: Annotated[str, Query(min_length=1, max_length=2048)],
) -> WorkMetadataProvenanceResponse:
    try:
        return await service.dossier_for_source(
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
