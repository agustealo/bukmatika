from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.library.portability_apply import (
    LibraryPortabilityImportApplier,
    LibraryPortabilityImportApplyResponse,
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


def portability_service() -> LibraryPortabilityService:
    return _portability_service


def import_planner() -> LibraryPortabilityImportPlanner:
    return _import_planner


def import_applier() -> LibraryPortabilityImportApplier:
    return _import_applier


@router.get("/export", response_model=LibraryPortabilityExportResponse)
async def export_library(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryPortabilityService, Depends(portability_service)],
) -> LibraryPortabilityExportResponse:
    return await service.export(principal_id=identity.principal_id)


@router.post("/import/plan", response_model=LibraryPortabilityImportPlanResponse)
async def plan_library_import(
    manifest: LibraryPortabilityExportResponse,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    planner: Annotated[LibraryPortabilityImportPlanner, Depends(import_planner)],
) -> LibraryPortabilityImportPlanResponse:
    return await planner.plan(principal_id=identity.principal_id, manifest=manifest)


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
