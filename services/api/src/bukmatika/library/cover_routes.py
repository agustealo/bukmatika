from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.covers import CoverNotFound, CoverService, CoverUnavailable
from bukmatika.persistence.library import DossierIdentityConflict, DossierNotFound

router = APIRouter(prefix="/v1/covers", tags=["covers"])


def cover_service(request: Request) -> CoverService:
    service = request.app.state.cover_service
    if not isinstance(service, CoverService):
        raise RuntimeError("Cover service is not initialized")
    return service


def _response(path: str) -> FileResponse:
    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/works/{work_id}", response_class=FileResponse)
async def work_cover(
    work_id: UUID,
    _identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[CoverService, Depends(cover_service)],
) -> FileResponse:
    try:
        cover = await service.cover_for_work(work_id=work_id)
    except CoverNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover not found") from exc
    except CoverUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "COVER_MATERIALIZATION_FAILED"},
        ) from exc
    return _response(str(cover.path))


@router.get("/source", response_class=FileResponse)
async def source_cover(
    provider: Annotated[str, Query(min_length=1, max_length=64)],
    record_id: Annotated[str, Query(min_length=1, max_length=2048)],
    _identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[CoverService, Depends(cover_service)],
) -> FileResponse:
    try:
        cover = await service.cover_for_source(
            provider=provider,
            provider_record_id=record_id,
        )
    except (CoverNotFound, DossierNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cover not found") from exc
    except DossierIdentityConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "COVER_SOURCE_IDENTITY_CONFLICT"},
        ) from exc
    except CoverUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "COVER_MATERIALIZATION_FAILED"},
        ) from exc
    return _response(str(cover.path))
