from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.local_import import (
    LOCAL_IMPORT_MEDIA_TYPE,
    LocalImportError,
    LocalImportResponse,
    LocalImportTooLarge,
    LocalLibraryImportService,
)
from bukmatika.persistence.local_import import LocalImportIdentityConflict

router = APIRouter(prefix="/v1/library", tags=["library-local-import"])
_settings = get_settings()
_local_import_service = LocalLibraryImportService(
    storage=LocalObjectStore(_settings.storage_root),
    settings=_settings,
)


def local_import_service() -> LocalLibraryImportService:
    return _local_import_service


@router.post(
    "/import/local",
    response_model=LocalImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_local_book(
    request: Request,
    response: Response,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LocalLibraryImportService, Depends(local_import_service)],
) -> LocalImportResponse:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != LOCAL_IMPORT_MEDIA_TYPE:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "LOCAL_IMPORT_MEDIA_TYPE_REQUIRED",
                "detail": f"Use {LOCAL_IMPORT_MEDIA_TYPE} for local book imports.",
            },
        )
    try:
        result = await service.import_stream(
            principal_id=identity.principal_id,
            stream=request.stream(),
        )
    except LocalImportTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": exc.code, "detail": exc.detail},
        ) from exc
    except LocalImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "detail": exc.detail},
        ) from exc
    except LocalImportIdentityConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "LOCAL_IMPORT_IDENTITY_CONFLICT", "detail": str(exc)},
        ) from exc
    if result.idempotent:
        response.status_code = status.HTTP_200_OK
    return result


__all__ = ["router"]
