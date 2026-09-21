from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from bukmatika.config import get_settings
from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import DiscoveryResponse, SearchIntent

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    client = httpx.AsyncClient(follow_redirects=False)
    app.state.http_client = client
    app.state.discovery = DiscoveryService([OpenLibraryAdapter(client, settings)])
    yield
    await client.aclose()


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


@app.post("/v1/discover", response_model=DiscoveryResponse)
async def discover(
    intent: SearchIntent,
    service: Annotated[DiscoveryService, Depends(discovery_service)],
) -> DiscoveryResponse:
    return await service.search(intent)
