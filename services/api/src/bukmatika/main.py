from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import Depends, FastAPI, Request

from bukmatika.config import Settings, get_settings
from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import DiscoveryResponse, SearchIntent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    client = httpx.AsyncClient(follow_redirects=False)
    app.state.http_client = client
    app.state.discovery = DiscoveryService([OpenLibraryAdapter(client, settings)])
    yield
    await client.aclose()


app = FastAPI(title="Bukmatika API", version="0.1.0", lifespan=lifespan)


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
    service: DiscoveryService = Depends(discovery_service),
) -> DiscoveryResponse:
    return await service.search(intent)
