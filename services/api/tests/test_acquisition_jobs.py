import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import AcquisitionStatus
from bukmatika.acquisition.downloader import SafeDownloader
from bukmatika.acquisition.jobs import AcquisitionJobWorker, AcquisitionQueueService
from bukmatika.acquisition.network import PinnedTarget
from bukmatika.acquisition.service import AcquisitionService
from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.catalog import CatalogResolver
from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence.acquisition import AcquisitionRepository
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.jobs import JobRepository, JobStatus
from bukmatika.persistence.models import Acquisition, Asset


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_open_asset(session: AsyncSession, suffix: str) -> Asset:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        DiscoveredRecord(
            candidate=DiscoveryCandidate(
                source="provider-jobs",
                source_record_id=f"edition-{suffix}",
                record_kind="edition",
                work_key=f"provider-jobs:work-{suffix}",
                identifiers={"isbn": [f"9780001{suffix.zfill(6)}"]},
                title=f"Job Work {suffix}",
                authors=["Worker Author"],
                landing_url=HttpUrl(f"https://example.org/{suffix}"),
                formats=["PDF"],
                assets=[
                    DiscoveredAsset(
                        name="book.pdf",
                        url=HttpUrl(f"https://files.example.org/{suffix}/book.pdf"),
                        format="PDF",
                        media_type="application/pdf",
                    )
                ],
                rights=[
                    RightsEvidence(
                        state=RightsState.OPEN_LICENSE,
                        source="provider-jobs",
                        basis="Exact asset is openly licensed for the worker test.",
                    )
                ],
            ),
            source_payload={"suffix": suffix},
            parser_version="test-jobs-v1",
        )
    )
    await session.flush()
    asset = await session.scalar(
        select(Asset).where(Asset.remote_url.contains(f"/{suffix}/book.pdf"))
    )
    assert asset is not None
    return asset


def _settings(tmp_path: Path, *, max_attempts: int = 2) -> Settings:
    return Settings(
        storage_root=tmp_path,
        acquisition_worker_enabled=False,
        acquisition_max_attempts=max_attempts,
        acquisition_retry_base_seconds=0.01,
        acquisition_retry_max_seconds=0.02,
        acquisition_job_lease_seconds=60,
        acquisition_job_heartbeat_seconds=10,
    )


def _target(url: str) -> PinnedTarget:
    return PinnedTarget(
        original_url=url,
        request_url="https://93.184.216.34/book.pdf",
        host_header="files.example.org",
        sni_hostname="files.example.org",
        resolved_ip="93.184.216.34",
    )


async def _worker_stack(
    session: AsyncSession,
    tmp_path: Path,
    handler: httpx.AsyncBaseTransport,
    *,
    max_attempts: int = 2,
) -> tuple[httpx.AsyncClient, AcquisitionQueueService, AcquisitionJobWorker]:
    async def resolver(url: str) -> PinnedTarget:
        return _target(url)

    client = httpx.AsyncClient(transport=handler)
    settings = _settings(tmp_path, max_attempts=max_attempts)
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
        settings,
        session_scope_factory=_scope(session),
    )
    queue = AcquisitionQueueService(settings, session_scope_factory=_scope(session))
    worker = AcquisitionJobWorker(
        service,
        settings,
        session_scope_factory=_scope(session),
    )
    return client, queue, worker


async def test_queue_worker_stores_real_acquisition_once(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    asset = await _seed_open_asset(session, "101")
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nworker stored",
            request=request,
        )

    client, queue, worker = await _worker_stack(
        session,
        tmp_path,
        httpx.MockTransport(handler),
    )
    try:
        queued = await queue.enqueue(asset.id)
        assert queued.job_id is not None
        assert queued.job_status == JobStatus.QUEUED.value
        assert await worker.run_once() is True
        state = await queue.get(queued.acquisition_id)
        job = await JobRepository(session).get(queued.job_id)
    finally:
        await client.aclose()

    assert state.status is AcquisitionStatus.STORED
    assert state.storage_key is not None
    assert job is not None
    assert job.status == JobStatus.COMPLETED.value
    assert request_count == 1


async def test_queued_acquisition_cancels_before_worker_claim(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    asset = await _seed_open_asset(session, "102")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"cancelled acquisition must not call network: {request.url}")

    client, queue, worker = await _worker_stack(
        session,
        tmp_path,
        httpx.MockTransport(handler),
    )
    try:
        queued = await queue.enqueue(asset.id)
        assert queued.job_id is not None
        cancelled = await queue.cancel(queued.acquisition_id)
        processed = await worker.run_once()
        job = await JobRepository(session).get(queued.job_id)
    finally:
        await client.aclose()

    assert cancelled.status is AcquisitionStatus.CANCELLED
    assert processed is False
    assert job is not None
    assert job.status == JobStatus.CANCELLED.value


async def test_transient_download_failure_retries_then_stores(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    asset = await _seed_open_asset(session, "103")
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nretry success",
            request=request,
        )

    client, queue, worker = await _worker_stack(
        session,
        tmp_path,
        httpx.MockTransport(handler),
    )
    try:
        queued = await queue.enqueue(asset.id)
        assert queued.job_id is not None
        assert await worker.run_once() is True
        first_job = await JobRepository(session).get(queued.job_id)
        assert first_job is not None
        assert first_job.status == JobStatus.QUEUED.value
        await asyncio.sleep(0.02)
        assert await worker.run_once() is True
        state = await queue.get(queued.acquisition_id)
        final_job = await JobRepository(session).get(queued.job_id)
    finally:
        await client.aclose()

    assert state.status is AcquisitionStatus.STORED
    assert final_job is not None
    assert final_job.status == JobStatus.COMPLETED.value
    assert final_job.attempt_count == 2
    assert request_count == 2


async def test_expired_worker_lease_recovers_orphaned_acquisition(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    asset = await _seed_open_asset(session, "104")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF-1.7\nrecovered",
            request=request,
        )

    client, queue, worker = await _worker_stack(
        session,
        tmp_path,
        httpx.MockTransport(handler),
        max_attempts=3,
    )
    try:
        queued = await queue.enqueue(asset.id)
        assert queued.job_id is not None
        jobs = JobRepository(session)
        first_lease = await jobs.claim_next(job_type="acquisition", lease_seconds=60)
        assert first_lease is not None
        acquisition = await AcquisitionRepository(session).get_acquisition(queued.acquisition_id)
        assert acquisition is not None
        acquisition.status = AcquisitionStatus.DOWNLOADING.value
        job = await jobs.get(first_lease.job_id)
        assert job is not None
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.flush()

        assert await worker.run_once() is True
        state = await queue.get(queued.acquisition_id)
        final_job = await jobs.get(first_lease.job_id)
    finally:
        await client.aclose()

    assert state.status is AcquisitionStatus.STORED
    assert final_job is not None
    assert final_job.status == JobStatus.COMPLETED.value
    assert final_job.attempt_count == 2
