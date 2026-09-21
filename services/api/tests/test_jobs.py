from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.jobs import JobLeaseLost, JobRepository, JobStatus


async def test_job_dedupes_claims_and_retries_with_owned_lease(session: AsyncSession) -> None:
    repository = JobRepository(session)
    first = await repository.enqueue(
        job_type="acquisition",
        payload={"asset_id": str(uuid4())},
        dedupe_key="test:job:dedupe",
        max_attempts=3,
    )
    second = await repository.enqueue(
        job_type="acquisition",
        payload={"asset_id": str(uuid4())},
        dedupe_key="test:job:dedupe",
        max_attempts=3,
    )
    assert first.id == second.id

    lease = await repository.claim_next(job_type="acquisition", lease_seconds=60)
    assert lease is not None
    assert lease.job_id == first.id
    assert lease.attempt_count == 1
    assert lease.recovered_from_expired_lease is False

    with pytest.raises(JobLeaseLost):
        await repository.heartbeat(
            job_id=lease.job_id,
            claim_token=uuid4(),
            lease_seconds=60,
        )

    retried = await repository.retry(
        job_id=lease.job_id,
        claim_token=lease.claim_token,
        error_code="REMOTE_DOWNLOAD_FAILED",
        error_detail="transient",
        delay_seconds=0,
    )
    assert retried.status == JobStatus.QUEUED.value

    second_lease = await repository.claim_next(job_type="acquisition", lease_seconds=60)
    assert second_lease is not None
    assert second_lease.job_id == lease.job_id
    assert second_lease.claim_token != lease.claim_token
    assert second_lease.attempt_count == 2


async def test_expired_running_job_is_reclaimed_with_new_token(session: AsyncSession) -> None:
    repository = JobRepository(session)
    job = await repository.enqueue(
        job_type="acquisition",
        payload={"asset_id": str(uuid4())},
        dedupe_key="test:job:expired",
        max_attempts=3,
    )
    first = await repository.claim_next(job_type="acquisition", lease_seconds=60)
    assert first is not None
    stored = await repository.get(job.id)
    assert stored is not None
    stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()

    recovered = await repository.claim_next(job_type="acquisition", lease_seconds=60)
    assert recovered is not None
    assert recovered.job_id == job.id
    assert recovered.claim_token != first.claim_token
    assert recovered.recovered_from_expired_lease is True
    assert recovered.attempt_count == 2

    with pytest.raises(JobLeaseLost):
        await repository.complete(job_id=job.id, claim_token=first.claim_token)


async def test_retry_exhaustion_becomes_terminal_failure(session: AsyncSession) -> None:
    repository = JobRepository(session)
    job = await repository.enqueue(
        job_type="acquisition",
        payload={"asset_id": str(uuid4())},
        dedupe_key="test:job:exhausted",
        max_attempts=1,
    )
    lease = await repository.claim_next(job_type="acquisition", lease_seconds=60)
    assert lease is not None
    failed = await repository.retry(
        job_id=job.id,
        claim_token=lease.claim_token,
        error_code="REMOTE_DOWNLOAD_FAILED",
        error_detail="still failing",
        delay_seconds=0,
    )
    assert failed.status == JobStatus.FAILED.value
    assert failed.completed_at is not None


async def test_queued_job_can_be_cancelled_before_claim(session: AsyncSession) -> None:
    repository = JobRepository(session)
    job = await repository.enqueue(
        job_type="acquisition",
        payload={"asset_id": str(uuid4())},
        dedupe_key="test:job:cancelled",
        max_attempts=3,
    )
    cancelled = await repository.cancel_if_queued(dedupe_key="test:job:cancelled")
    assert cancelled is not None
    assert cancelled.id == job.id
    assert cancelled.status == JobStatus.CANCELLED.value
    assert await repository.claim_next(job_type="acquisition", lease_seconds=60) is None
