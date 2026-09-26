from fastapi import APIRouter

from bukmatika.library.citation_routes import router as citation_router
from bukmatika.library.citations import CitationExportFormat, LibraryCitationExportService
from bukmatika.library.cover_routes import router as cover_router
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
from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.library.portability_apply import (
    LibraryPortabilityImportApplier,
    LibraryPortabilityImportApplyResponse,
    LibraryPortabilityImportApplySummary,
)
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    LibraryPortabilityImportPlanResponse,
)
from bukmatika.library.portability_import import LibraryPortabilityImportPlanner
from bukmatika.library.portability_routes import router as portability_router
from bukmatika.library.provenance import MetadataProvenanceService
from bukmatika.library.provenance_domain import (
    EditionMetadataProvenanceResponse,
    MetadataAssertionResponse,
    WorkMetadataProvenanceResponse,
)
from bukmatika.library.provenance_routes import router as provenance_router
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
router.include_router(portability_router)
router.include_router(provenance_router)
router.include_router(cover_router)
router.include_router(citation_router)

__all__ = [
    "AssetStatusResponse",
    "CitationExportFormat",
    "CollectionCreate",
    "CollectionResponse",
    "CollectionSummaryResponse",
    "CollectionUpdate",
    "EditionDossierResponse",
    "EditionMetadataProvenanceResponse",
    "LibraryAssetAggregateStatus",
    "LibraryAssetStage",
    "LibraryAssetStatusItem",
    "LibraryCitationExportService",
    "LibraryItemResponse",
    "LibraryOrganizationConflict",
    "LibraryOrganizationNotFound",
    "LibraryOrganizationResponse",
    "LibraryPortabilityExportResponse",
    "LibraryPortabilityImportApplier",
    "LibraryPortabilityImportApplyResponse",
    "LibraryPortabilityImportApplySummary",
    "LibraryPortabilityImportPlanResponse",
    "LibraryPortabilityImportPlanner",
    "LibraryPortabilityService",
    "LibraryReadingStatus",
    "LibraryResponse",
    "LibraryService",
    "LibraryStatusResponse",
    "LibraryStatusService",
    "LibraryStatusSummary",
    "MetadataAssertionResponse",
    "MetadataProvenanceService",
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
    "WorkMetadataProvenanceResponse",
    "router",
]
