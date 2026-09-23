from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.library.portability_apply import (
    LibraryPortabilityImportApplier,
    LibraryPortabilityImportApplyResponse,
)
from bukmatika.library.portability_bundle import (
    BUNDLE_MEDIA_TYPE,
    LibraryPortabilityBundleApplyResponse,
    LibraryPortabilityBundlePlanResponse,
    LibraryPortabilityBundleService,
    PortabilityBundleError,
)
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    LibraryPortabilityImportPlanResponse,
)
from bukmatika.library.portability_import import LibraryPortabilityImportPlanner

router = APIRouter(prefix="/v1/library", tags=["library-portability"])
_portability_service = LibraryPortabilityService()
_import_planner = LibraryPortabilityImportPlanner()
_import_applier = LibraryPortabilityImportApplier()
_bundle_service = LibraryPortabilityBundleService(settings=get_settings())


def portability_service() -> LibraryPortabilityService:
    return _portability_service


def import_planner() -> LibraryPortabilityImportPlanner:
    return _import_planner


def import_applier() -> LibraryPortabilityImportApplier:
    return _import_applier


def bundle_service() -> LibraryPortabilityBundleService:
    return _bundle_service


@router.get("/export", response_model=LibraryPortabilityExportResponse)
async def export_library(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryPortabilityService, Depends(portability_service)],
) -> LibraryPortabilityExportResponse:
    return await service.export(principal_id=identity.principal_id)


@router.get("/export/file", response_class=FileResponse)
async def export_library_file(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryPortabilityBundleService, Depends(bundle_service)],
) -> FileResponse:
    artifact = await service.export_bundle(principal_id=identity.principal_id)
    return FileResponse(
        artifact.path,
        media_type=BUNDLE_MEDIA_TYPE,
        filename=artifact.filename,
        background=BackgroundTask(artifact.cleanup),
    )


@router.post("/import/plan", response_model=LibraryPortabilityImportPlanResponse)
async def plan_library_import(
    manifest: LibraryPortabilityExportResponse,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    planner: Annotated[LibraryPortabilityImportPlanner, Depends(import_planner)],
) -> LibraryPortabilityImportPlanResponse:
    return await planner.plan(principal_id=identity.principal_id, manifest=manifest)


@router.post("/import/file/plan", response_model=LibraryPortabilityBundlePlanResponse)
async def plan_library_file_import(
    request: Request,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryPortabilityBundleService, Depends(bundle_service)],
) -> LibraryPortabilityBundlePlanResponse:
    bundle_path: Path | None = None
    upload_root: Path | None = None
    inspection = None
    try:
        bundle_path, upload_root = await service.receive_bundle(request.stream())
        inspection = await service.inspect_bundle(bundle_path=bundle_path)
        return await service.plan_bundle(
            principal_id=identity.principal_id,
            inspection=inspection,
        )
    except PortabilityBundleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "detail": exc.detail},
        ) from exc
    finally:
        if inspection is not None:
            inspection.cleanup()
        if upload_root is not None:
            service.cleanup_upload(upload_root)


@router.post("/import/apply", response_model=LibraryPortabilityImportApplyResponse)
async def apply_library_import(
    manifest: LibraryPortabilityExportResponse,
    response: Response,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    applier: Annotated[LibraryPortabilityImportApplier, Depends(import_applier)],
) -> LibraryPortabilityImportApplyResponse:
    result = await applier.apply(principal_id=identity.principal_id, manifest=manifest)
    if not result.committed:
        response.status_code = status.HTTP_409_CONFLICT
    return result


@router.post("/import/file/apply", response_model=LibraryPortabilityBundleApplyResponse)
async def apply_library_file_import(
    request: Request,
    response: Response,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryPortabilityBundleService, Depends(bundle_service)],
) -> LibraryPortabilityBundleApplyResponse:
    upload_root: Path | None = None
    inspection = None
    try:
        bundle_path, upload_root = await service.receive_bundle(request.stream())
        inspection = await service.inspect_bundle(bundle_path=bundle_path)
        result = await service.apply_bundle(
            principal_id=identity.principal_id,
            inspection=inspection,
        )
        if not result.manifest.committed:
            response.status_code = status.HTTP_409_CONFLICT
        return result
    except PortabilityBundleError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "detail": exc.detail},
        ) from exc
    finally:
        if inspection is not None:
            inspection.cleanup()
        if upload_root is not None:
            service.cleanup_upload(upload_root)
