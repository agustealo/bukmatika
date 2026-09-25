import asyncio
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware

from bukmatika.acquisition import (
    AcquisitionPolicyResponse,
    AcquisitionPolicyUpdate,
    AcquisitionRequestResponse,
    AcquisitionService,
    AssetNotFound,
    PrincipalAcquisitionRequestConflict,
    PrincipalAcquisitionRequestNotFound,
    PrincipalAcquisitionService,
)
from bukmatika.acquisition.downloader import SafeDownloader
from bukmatika.acquisition.jobs import AcquisitionJobWorker, AcquisitionQueueService
from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.ai.delegation_jobs import DelegationJobWorker
from bukmatika.ai.factory import build_model_gateway
from bukmatika.ai.research_workspace_routes import router as research_workspace_answer_router
from bukmatika.ai.routes import router as ai_router
from bukmatika.catalog import CatalogResolver
from bukmatika.config import get_settings
from bukmatika.discovery.gutenberg import ProjectGutenbergAdapter
from bukmatika.discovery.internet_archive import InternetArchiveAdapter
from bukmatika.discovery.library_of_congress import LibraryOfCongressAdapter
from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import (
    CatalogSearchItem,
    CatalogSearchResponse,
    DiscoveryResponse,
    SearchIntent,
)
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.identity import router as identity_router
from bukmatika.library import router as library_router
from bukmatika.observability import (
    REQUEST_ID_HEADER,
    RequestCorrelationMiddleware,
    configure_logging,
    correlated_internal_server_error,
)
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition import AcquisitionStateConflict
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.search import CatalogSearchRepository
from bukmatika.personalization.routes import router as personalization_router
from bukmatika.processing import (
    AssetNotStored,
    DocumentParseError,
    DocumentProcessingService,
    DocumentRequiresOCR,
    DocumentResponse,
    DocumentSearchResponse,
    DocxDocumentParser,
    EpubDocumentParser,
    HtmlDocumentParser,
    OcrAssetNotFound,
    OcrJobNotFound,
    OcrJobResponse,
    OcrNotEligible,
    OcrQueueService,
    ParserRegistry,
    PdfDocumentParser,
    ProcessedDocumentNotFound,
    ProcessingAssetNotFound,
    ProcessingSourceChanged,
    ProcessingTimedOut,
    StoredObjectUnavailable,
    TextDocumentParser,
    UnsupportedDocumentFormat,
)
from bukmatika.reader.routes import router as reader_router
from bukmatika.research import router as research_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging(level=settings.log_level)
    discovery_client = httpx.AsyncClient(follow_redirects=False)
    acquisition_client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    model_client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    app.state.http_client = discovery_client
    app.state.model_gateway = build_model_gateway(settings=settings, client=model_client)
    registry = ProviderRegistry(
        [
            ProviderRegistration(
                adapter=OpenLibraryAdapter(discovery_client, settings),
                max_results=settings.discovery_provider_result_limit,
                timeout_seconds=settings.http_timeout_seconds,
            ),
            ProviderRegistration(
                adapter=InternetArchiveAdapter(discovery_client, settings),
                max_results=settings.discovery_provider_result_limit,
                timeout_seconds=settings.http_timeout_seconds,
            ),
            ProviderRegistration(
                adapter=ProjectGutenbergAdapter(discovery_client, settings),
                max_results=settings.discovery_provider_result_limit,
                timeout_seconds=settings.http_timeout_seconds,
            ),
            ProviderRegistration(
                adapter=LibraryOfCongressAdapter(discovery_client, settings),
                max_results=settings.discovery_provider_result_limit,
                timeout_seconds=settings.http_timeout_seconds,
            ),
        ]
    )
    app.state.discovery = DiscoveryService(
        registry,
        session_timeout_seconds=settings.discovery_session_timeout_seconds,
        max_records=settings.discovery_max_records,
    )
    object_store = LocalObjectStore(settings.storage_root)
    acquisition_executor = AcquisitionService(
        SafeDownloader(
            acquisition_client,
            max_bytes=settings.acquisition_max_bytes,
            redirect_limit=settings.acquisition_redirect_limit,
            chunk_size=settings.acquisition_chunk_size,
            timeout_seconds=settings.acquisition_timeout_seconds,
            user_agent=_user_agent(),
        ),
        object_store,
        settings,
    )
    acquisition_queue = AcquisitionQueueService(settings)
    app.state.acquisition_queue = acquisition_queue
    app.state.principal_acquisition = PrincipalAcquisitionService(
        settings,
        queue=acquisition_queue,
    )
    app.state.ocr_queue = OcrQueueService(settings)
    app.state.document_processing = DocumentProcessingService(
        ParserRegistry(
            (
                TextDocumentParser(),
                HtmlDocumentParser(),
                PdfDocumentParser(),
                EpubDocumentParser(),
                DocxDocumentParser(),
            )
        ),
        object_store,
        settings,
    )
    worker_tasks: list[asyncio.Task[None]] = []
    if settings.acquisition_worker_enabled:
        acquisition_worker = AcquisitionJobWorker(acquisition_executor, settings)
        worker_tasks.append(
            asyncio.create_task(
                acquisition_worker.run(),
                name="bukmatika-acquisition-worker",
            )
        )
    if settings.delegation_worker_enabled:
        delegation_worker = DelegationJobWorker(settings)
        worker_tasks.append(
            asyncio.create_task(
                delegation_worker.run(),
                name="bukmatika-delegation-worker",
            )
        )
    try:
        yield
    finally:
        for worker_task in worker_tasks:
            worker_task.cancel()
        for worker_task in worker_tasks:
            with suppress(asyncio.CancelledError):
                await worker_task
        await model_client.aclose()
        await acquisition_client.aclose()
        await discovery_client.aclose()


app = FastAPI(
    title="Bukmatika API",
    version="0.1.0",
    lifespan=lifespan,
    exception_handlers={Exception: correlated_internal_server_error},
)
app.include_router(identity_router)
app.include_router(library_router)
app.include_router(reader_router)
app.include_router(research_router)
app.include_router(personalization_router)
app.include_router(ai_router)
app.include_router(research_workspace_answer_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", REQUEST_ID_HEADER],
    expose_headers=[REQUEST_ID_HEADER],
)
app.add_middleware(RequestCorrelationMiddleware)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def discovery_service(request: Request) -> DiscoveryService:
    service = request.app.state.discovery
    if not isinstance(service, DiscoveryService):
        raise RuntimeError("Discovery service is not initialized")
    return service


def principal_acquisition_service(request: Request) -> PrincipalAcquisitionService:
    service = request.app.state.principal_acquisition
    if not isinstance(service, PrincipalAcquisitionService):
        raise RuntimeError("Principal acquisition service is not initialized")
    return service


def ocr_queue_service(request: Request) -> OcrQueueService:
    service = request.app.state.ocr_queue
    if not isinstance(service, OcrQueueService):
        raise RuntimeError("OCR queue service is not initialized")
    return service


def document_processing_service(request: Request) -> DocumentProcessingService:
    service = request.app.state.document_processing
    if not isinstance(service, DocumentProcessingService):
        raise RuntimeError("Document processing service is not initialized")
    return service


@app.post("/v1/discover", response_model=DiscoveryResponse)
async def discover(
    intent: SearchIntent,
    service: Annotated[DiscoveryService, Depends(discovery_service)],
) -> DiscoveryResponse:
    search_session = service.create_session()
    submitted_context = {
        "session_id": str(search_session.id),
        "intent": intent.model_dump(mode="json"),
    }
    async with session_scope() as database_session:
        await InteractionEventRepository(database_session).record(
            SemanticEventType.DISCOVERY_SEARCH_SUBMITTED,
            context=submitted_context,
        )

    try:
        batch = await service.discover(intent, search_session)
        async with session_scope() as database_session:
            resolver = CatalogResolver(CatalogRepository(database_session))
            for record in batch.records:
                await resolver.ingest(record)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.DISCOVERY_SEARCH_COMPLETED,
                context={
                    "session_id": str(search_session.id),
                    "result_count": len(batch.response.candidates),
                    "persisted_record_count": len(batch.records),
                    "sources_queried": batch.response.sources_queried,
                    "source_errors": batch.response.source_errors,
                    "elapsed_ms": batch.response.elapsed_ms,
                },
            )
        return batch.response
    except Exception as exc:
        async with session_scope() as database_session:
            await InteractionEventRepository(database_session).record(
                SemanticEventType.DISCOVERY_SEARCH_FAILED,
                context={
                    "session_id": str(search_session.id),
                    "error_type": type(exc).__name__,
                },
            )
        raise


@app.get("/v1/catalog/search", response_model=CatalogSearchResponse)
async def catalog_search(
    q: Annotated[str, Query(min_length=1, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=100)] = settings.catalog_search_default_limit,
) -> CatalogSearchResponse:
    query = " ".join(q.split())
    normalized_query = _normalize_text(query)
    async with session_scope() as database_session:
        events = InteractionEventRepository(database_session)
        await events.record(
            SemanticEventType.CATALOG_SEARCH_SUBMITTED,
            context={"query": query, "limit": limit},
        )
        search = CatalogSearchRepository(database_session)
        matches = await search.search_works(
            query=query,
            normalized_query=normalized_query,
            limit=limit,
        )
        items = [
            CatalogSearchItem(
                work_id=match.work_id,
                title=match.title,
                authors=await search.authors_for_work(match.work_id),
                score=max(0.0, match.score),
            )
            for match in matches
        ]
        await events.record(
            SemanticEventType.CATALOG_SEARCH_COMPLETED,
            context={"query": query, "result_count": len(items)},
        )
    return CatalogSearchResponse(query=query, items=items)


@app.get("/v1/acquisition-policy", response_model=AcquisitionPolicyResponse)
async def acquisition_policy(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PrincipalAcquisitionService, Depends(principal_acquisition_service)],
) -> AcquisitionPolicyResponse:
    return await service.policy(principal_id=identity.principal_id)


@app.post("/v1/acquisition-policy", response_model=AcquisitionPolicyResponse)
async def update_acquisition_policy(
    update: AcquisitionPolicyUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PrincipalAcquisitionService, Depends(principal_acquisition_service)],
) -> AcquisitionPolicyResponse:
    return await service.update_policy(
        principal_id=identity.principal_id,
        update=update,
    )


@app.post(
    "/v1/assets/{asset_id}/acquire",
    response_model=AcquisitionRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def acquire_asset(
    asset_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PrincipalAcquisitionService, Depends(principal_acquisition_service)],
) -> AcquisitionRequestResponse:
    try:
        return await service.request_asset(
            principal_id=identity.principal_id,
            asset_id=asset_id,
        )
    except AssetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        ) from exc
    except AcquisitionStateConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ACQUISITION_STATE_CONFLICT"},
        ) from exc


@app.get(
    "/v1/acquisition-requests/{request_id}",
    response_model=AcquisitionRequestResponse,
)
async def acquisition_request_status(
    request_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PrincipalAcquisitionService, Depends(principal_acquisition_service)],
) -> AcquisitionRequestResponse:
    try:
        return await service.get(
            principal_id=identity.principal_id,
            request_id=request_id,
        )
    except (PrincipalAcquisitionRequestNotFound, AssetNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Acquisition request not found",
        ) from exc


@app.post(
    "/v1/acquisition-requests/{request_id}/approve",
    response_model=AcquisitionRequestResponse,
)
async def approve_acquisition_request(
    request_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PrincipalAcquisitionService, Depends(principal_acquisition_service)],
) -> AcquisitionRequestResponse:
    try:
        return await service.approve(
            principal_id=identity.principal_id,
            request_id=request_id,
        )
    except (PrincipalAcquisitionRequestNotFound, AssetNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Acquisition request not found",
        ) from exc
    except PrincipalAcquisitionRequestConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ACQUISITION_REQUEST_STATE_CONFLICT"},
        ) from exc
    except AcquisitionStateConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ACQUISITION_STATE_CONFLICT"},
        ) from exc


@app.post(
    "/v1/acquisition-requests/{request_id}/cancel",
    response_model=AcquisitionRequestResponse,
)
async def cancel_acquisition_request(
    request_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[PrincipalAcquisitionService, Depends(principal_acquisition_service)],
) -> AcquisitionRequestResponse:
    try:
        return await service.cancel(
            principal_id=identity.principal_id,
            request_id=request_id,
        )
    except (PrincipalAcquisitionRequestNotFound, AssetNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Acquisition request not found",
        ) from exc


@app.post("/v1/assets/{asset_id}/process", response_model=DocumentResponse)
async def process_asset(
    asset_id: UUID,
    service: Annotated[DocumentProcessingService, Depends(document_processing_service)],
) -> DocumentResponse:
    try:
        return await service.process_asset(asset_id)
    except ProcessingAssetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        ) from exc
    except AssetNotStored as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ASSET_NOT_STORED"},
        ) from exc
    except StoredObjectUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "STORED_OBJECT_UNAVAILABLE"},
        ) from exc
    except ProcessingSourceChanged as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "PROCESSING_SOURCE_CHANGED"},
        ) from exc
    except UnsupportedDocumentFormat as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "UNSUPPORTED_DOCUMENT_FORMAT"},
        ) from exc
    except DocumentRequiresOCR as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "DOCUMENT_REQUIRES_OCR"},
        ) from exc
    except DocumentParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "DOCUMENT_PARSE_FAILED"},
        ) from exc
    except ProcessingTimedOut as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail={"code": "DOCUMENT_PROCESSING_TIMEOUT"},
        ) from exc


@app.post(
    "/v1/assets/{asset_id}/ocr",
    response_model=OcrJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_ocr(
    asset_id: UUID,
    service: Annotated[OcrQueueService, Depends(ocr_queue_service)],
) -> OcrJobResponse:
    try:
        return await service.enqueue(asset_id)
    except OcrAssetNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        ) from exc
    except OcrNotEligible as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OCR_NOT_ELIGIBLE"},
        ) from exc


@app.get("/v1/ocr-jobs/{job_id}", response_model=OcrJobResponse)
async def ocr_status(
    job_id: UUID,
    service: Annotated[OcrQueueService, Depends(ocr_queue_service)],
) -> OcrJobResponse:
    try:
        return await service.get(job_id)
    except OcrJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OCR job not found",
        ) from exc


@app.get("/v1/documents/{document_id}/search", response_model=DocumentSearchResponse)
async def search_document(
    document_id: UUID,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    service: Annotated[DocumentProcessingService, Depends(document_processing_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = settings.document_search_default_limit,
) -> DocumentSearchResponse:
    try:
        return await service.search_document(document_id, query=q, limit=limit)
    except ProcessedDocumentNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        ) from exc


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _user_agent() -> str:
    if settings.contact_email:
        return f"{settings.user_agent} ({settings.contact_email})"
    return settings.user_agent
