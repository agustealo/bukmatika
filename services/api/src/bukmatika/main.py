import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware

from bukmatika.acquisition import (
    AcquisitionDenied,
    AcquisitionExecutionError,
    AcquisitionResponse,
    AcquisitionService,
    AssetNotFound,
)
from bukmatika.acquisition.downloader import SafeDownloader
from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.catalog import CatalogResolver
from bukmatika.config import get_settings
from bukmatika.discovery.internet_archive import InternetArchiveAdapter
from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import (
    CatalogSearchItem,
    CatalogSearchResponse,
    DiscoveryResponse,
    SearchIntent,
)
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition import AcquisitionStateConflict
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.search import CatalogSearchRepository

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    discovery_client = httpx.AsyncClient(follow_redirects=False)
    acquisition_client = httpx.AsyncClient(follow_redirects=False, trust_env=False)
    app.state.http_client = discovery_client
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
        ]
    )
    app.state.discovery = DiscoveryService(
        registry,
        session_timeout_seconds=settings.discovery_session_timeout_seconds,
        max_records=settings.discovery_max_records,
    )
    app.state.acquisition = AcquisitionService(
        SafeDownloader(
            acquisition_client,
            max_bytes=settings.acquisition_max_bytes,
            redirect_limit=settings.acquisition_redirect_limit,
            chunk_size=settings.acquisition_chunk_size,
            timeout_seconds=settings.acquisition_timeout_seconds,
            user_agent=_user_agent(),
        ),
        LocalObjectStore(settings.storage_root),
        settings,
    )
    try:
        yield
    finally:
        await acquisition_client.aclose()
        await discovery_client.aclose()


app = FastAPI(title="Bukmatika API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def discovery_service(request: Request) -> DiscoveryService:
    service = request.app.state.discovery
    if not isinstance(service, DiscoveryService):
        raise RuntimeError("Discovery service is not initialized")
    return service


def acquisition_service(request: Request) -> AcquisitionService:
    service = request.app.state.acquisition
    if not isinstance(service, AcquisitionService):
        raise RuntimeError("Acquisition service is not initialized")
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


@app.post("/v1/assets/{asset_id}/acquire", response_model=AcquisitionResponse)
async def acquire_asset(
    asset_id: UUID,
    service: Annotated[AcquisitionService, Depends(acquisition_service)],
) -> AcquisitionResponse:
    try:
        return await service.acquire(asset_id)
    except AssetNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found") from exc
    except AcquisitionDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "RIGHTS_DENIED",
                "acquisition_id": str(exc.acquisition_id),
                "rights_state": exc.rights_state.value,
            },
        ) from exc
    except AcquisitionStateConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ACQUISITION_STATE_CONFLICT"},
        ) from exc
    except AcquisitionExecutionError as exc:
        raise HTTPException(
            status_code=_acquisition_http_status(exc.error_code),
            detail={"code": exc.error_code},
        ) from exc


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _user_agent() -> str:
    if settings.contact_email:
        return f"{settings.user_agent} ({settings.contact_email})"
    return settings.user_agent


def _acquisition_http_status(error_code: str) -> int:
    if error_code == "ASSET_TOO_LARGE":
        return status.HTTP_413_CONTENT_TOO_LARGE
    if error_code in {"UNSAFE_REMOTE_URL", "FORMAT_VERIFICATION_FAILED"}:
        return status.HTTP_422_UNPROCESSABLE_CONTENT
    if error_code == "STORAGE_FAILED":
        return status.HTTP_500_INTERNAL_SERVER_ERROR
    return status.HTTP_502_BAD_GATEWAY
