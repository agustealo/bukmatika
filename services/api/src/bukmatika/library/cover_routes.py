from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from bukmatika.acquisition.downloader import SafeDownloader
from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.covers import CoverCache, CoverNotFound, CoverService, CoverUnavailable
from bukmatika.persistence.library import DossierIdentityConflict, DossierNotFound

router = APIRouter(prefix="/v1/covers", tags=["covers"])


def _user_agent() -> str:
    settings = get_settings()
    if settings.contact_email:
        return f"{settings.user_agent} ({settings.contact_email})"
    return settings.user_agent


@asynccontextmanager
async def _cover_service() -> AsyncIterator[CoverService]:
    settings = get_settings()
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        yield CoverService(
            downloader=SafeDownloader(
                client,
                max_bytes=settings.cover_max_bytes,
                redirect_limit=settings.cover_redirect_limit,
                chunk_size=65_536,
                timeout_seconds=settings.cover_timeout_seconds,
                user_agent=_user_agent(),
                resume_limit=0,
            ),
            cache=CoverCache(settings.storage_root),
            max_source_pixels=settings.cover_max_source_pixels,
            max_dimension=settings.cover_max_dimension,
            max_cached_bytes=settings.cover_max_cached_bytes,
        )


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
) -> FileResponse:
    try:
        async with _cover_service() as service:
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
) -> FileResponse:
    try:
        async with _cover_service() as service:
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
