import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, status

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.privacy.domain import (
    AccountDeleteRequest,
    AccountDeleteResponse,
    AccountPrivacyExportResponse,
)
from bukmatika.privacy.service import AccountPrivacyService, PrincipalNotFound
from bukmatika.privacy.worker import PrivacyErasureWorker

settings = get_settings()
_account_privacy_service = AccountPrivacyService(storage=LocalObjectStore(settings.storage_root))


@asynccontextmanager
async def privacy_lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker_task: asyncio.Task[None] | None = None
    if settings.privacy_erasure_worker_enabled:
        worker = PrivacyErasureWorker(
            _account_privacy_service,
            poll_seconds=settings.privacy_erasure_worker_poll_seconds,
        )
        worker_task = asyncio.create_task(
            worker.run(),
            name="bukmatika-privacy-erasure-worker",
        )
    try:
        yield
    finally:
        if worker_task is not None:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task


router = APIRouter(prefix="/account", tags=["privacy"], lifespan=privacy_lifespan)


def privacy_service() -> AccountPrivacyService:
    return _account_privacy_service


@router.get("/export", response_model=AccountPrivacyExportResponse)
async def export_account_data(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[AccountPrivacyService, Depends(privacy_service)],
) -> AccountPrivacyExportResponse:
    await service.cleanup_pending_storage()
    try:
        return await service.export(principal_id=identity.principal_id)
    except PrincipalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRINCIPAL_NOT_FOUND"},
        ) from exc


@router.post("/delete", response_model=AccountDeleteResponse)
async def delete_account_data(
    deletion: AccountDeleteRequest,
    response: Response,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[AccountPrivacyService, Depends(privacy_service)],
) -> AccountDeleteResponse:
    _ = deletion
    try:
        result = await service.delete_account(principal_id=identity.principal_id)
    except PrincipalNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PRINCIPAL_NOT_FOUND"},
        ) from exc
    response.delete_cookie(
        key=settings.local_session_cookie_name,
        path="/",
        secure=settings.local_session_secure_cookie,
        httponly=True,
        samesite="lax",
    )
    return result


__all__ = ["privacy_service", "router"]
