from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import AcquisitionStatus
from bukmatika.acquisition.downloader import SafeDownloader
from bukmatika.acquisition.network import PinnedTarget
from bukmatika.acquisition.service import AcquisitionDenied, AcquisitionService
from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.catalog import CatalogResolver
from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Acquisition, Asset, RightsDecision, StoredObject


async def _seed_asset(session: AsyncSession, rights_state: RightsState) -> Asset:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        DiscoveredRecord(
            candidate=DiscoveryCandidate(
                source="provider-test",
                source_record_id=f"edition-{rights_state.value}",
                record_kind="edition",
                work_key=f"provider-test:work-{rights_state.value}",
                identifiers={"isbn": [f"97800000002{len(rights_state.value):02d}"]},
                title=f"{rights_state.value} Work",
                authors=["Test Author"],
                landing_url=HttpUrl(f"https://example.org/{rights_state.value}"),
                formats=["PDF"],
                assets=[
                    DiscoveredAsset(
                        name="book.pdf",
                        url=HttpUrl(f"https://files.example.org/{rights_state.value}/book.pdf"),
                        format="PDF",
                        media_type="application/pdf",
                    )
                ],
                rights=[
                    RightsEvidence(
                        state=rights_state,
                        source="provider-test",
                        basis="Test exact-item rights evidence.",
                    )
                ],
            ),
            source_payload={"rights": rights_state.value},
            parser_version="test-v1",
        )
    )
    await session.flush()
    asset = await session.scalar(
        select(Asset).where(Asset.remote_url.contains(f"/{rights_state.value}/"))
    )
    assert asset is not None
    return asset


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _pinned(url: str) -> PinnedTarget:
    return PinnedTarget(
        original_url=url,
        request_url="https://93.184.216.34/book.pdf",
        host_header="files.example.org",
        sni_hostname="files.example.org",
        resolved_ip="93.184.216.34",
    )


async def test_unknown_rights_deny_before_network(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    asset = await _seed_asset(session, RightsState.UNKNOWN)
    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500, request=request)

    async def resolver(url: str) -> PinnedTarget:
        return _pinned(url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = AcquisitionService(
            SafeDownloader(
                client,
                max_bytes=1024,
                redirect_limit=1,
                chunk_size=64,
                timeout_seconds=5,
                user_agent="Bukmatika-Test",
                resolver=resolver,
            ),
            LocalObjectStore(tmp_path),
            Settings(storage_root=tmp_path),
            session_scope_factory=_scope(session),
        )
        with pytest.raises(AcquisitionDenied) as denied:
            await service.acquire(asset.id)

    assert denied.value.rights_state is RightsState.UNKNOWN
    assert request_count == 0
    acquisition = await session.scalar(select(Acquisition).where(Acquisition.asset_id == asset.id))
    decision = await session.scalar(
        select(RightsDecision).where(
            RightsDecision.subject_type == "asset",
            RightsDecision.subject_id == asset.id,
        )
    )
    assert acquisition is not None
    assert acquisition.status == AcquisitionStatus.FAILED.value
    assert acquisition.error_code == "RIGHTS_DENIED"
    assert decision is not None
    assert decision.permissions["download"] is False


async def test_open_license_downloads_stores_and_is_idempotent(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    asset = await _seed_asset(session, RightsState.OPEN_LICENSE)
    request_count = 0
    body = b"%PDF-1.7\nBukmatika verified content"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=body,
            request=request,
        )

    async def resolver(url: str) -> PinnedTarget:
        return _pinned(url)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = AcquisitionService(
            SafeDownloader(
                client,
                max_bytes=1024,
                redirect_limit=1,
                chunk_size=64,
                timeout_seconds=5,
                user_agent="Bukmatika-Test",
                resolver=resolver,
            ),
            LocalObjectStore(tmp_path),
            Settings(storage_root=tmp_path),
            session_scope_factory=_scope(session),
        )
        first = await service.acquire(asset.id)
        second = await service.acquire(asset.id)

    assert first.status is AcquisitionStatus.STORED
    assert second.status is AcquisitionStatus.STORED
    assert request_count == 1
    assert first.sha256 is not None
    assert first.storage_key is not None
    assert (tmp_path / first.storage_key).read_bytes() == body

    acquisition = await session.scalar(select(Acquisition).where(Acquisition.asset_id == asset.id))
    stored = await session.scalar(select(StoredObject).where(StoredObject.sha256 == first.sha256))
    refreshed_asset = await session.get(Asset, asset.id)
    assert acquisition is not None
    assert acquisition.status == AcquisitionStatus.STORED.value
    assert stored is not None
    assert refreshed_asset is not None
    assert refreshed_asset.stored_object_id == stored.id
