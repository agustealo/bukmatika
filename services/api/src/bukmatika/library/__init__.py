from fastapi import APIRouter

from bukmatika.library.domain import (
    AssetStatusResponse,
    CollectionCreate,
    CollectionResponse,
    CollectionSummaryResponse,
    CollectionUpdate,
    EditionDossierResponse,
    LibraryAssetAggregateStatus,
    LibraryAssetStage,
    LibraryAssetStatusItem,
    LibraryItemResponse,
    LibraryOrganizationResponse,
    LibraryReadingStatus,
    LibraryResponse,
    LibraryStatusResponse,
    LibraryStatusSummary,
    SmartShelfContentsResponse,
    SmartShelfCreate,
    SmartShelfResponse,
    SmartShelfRule,
    SmartShelfUpdate,
    TagAssignRequest,
    TagResponse,
    TagSummaryResponse,
    TagUpdate,
    WorkDossierResponse,
)
from bukmatika.library.routes import router as library_router
from bukmatika.library.service import (
    LibraryOrganizationConflict,
    LibraryOrganizationNotFound,
    LibraryService,
)
from bukmatika.library.status import LibraryStatusService
from bukmatika.library.status_routes import router as status_router

router = APIRouter()
router.include_router(library_router)
router.include_router(status_router)

__all__ = [
    "AssetStatusResponse",
    "CollectionCreate",
    "CollectionResponse",
    "CollectionSummaryResponse",
    "CollectionUpdate",
    "EditionDossierResponse",
    "LibraryAssetAggregateStatus",
    "LibraryAssetStage",
    "LibraryAssetStatusItem",
    "LibraryItemResponse",
    "LibraryOrganizationConflict",
    "LibraryOrganizationNotFound",
    "LibraryOrganizationResponse",
    "LibraryReadingStatus",
    "LibraryResponse",
    "LibraryService",
    "LibraryStatusResponse",
    "LibraryStatusService",
    "LibraryStatusSummary",
    "SmartShelfContentsResponse",
    "SmartShelfCreate",
    "SmartShelfResponse",
    "SmartShelfRule",
    "SmartShelfUpdate",
    "TagAssignRequest",
    "TagResponse",
    "TagSummaryResponse",
    "TagUpdate",
    "WorkDossierResponse",
    "router",
]
