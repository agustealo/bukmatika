from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled')",
            name="ck_job_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_job_attempt_count"),
        CheckConstraint("max_attempts >= 1", name="ck_job_max_attempts"),
        Index(
            "ix_jobs_claimable",
            "status",
            "available_at",
            "lease_expires_at",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=JobStatus.QUEUED.value, server_default="queued"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: UUID
    claim_token: UUID
    job_type: str
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int
    recovered_from_expired_lease: bool


class JobLeaseLost(RuntimeError):
    pass


class JobRepository:
    """Canonical PostgreSQL queue with ownership tokens and expiring leases."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        max_attempts: int,
        revive_terminal: bool = True,
    ) -> Job:
        now = datetime.now(UTC)
        statement = (
            insert(Job)
            .values(
                job_type=job_type,
                payload=payload,
                dedupe_key=dedupe_key,
                status=JobStatus.QUEUED.value,
                attempt_count=0,
                max_attempts=max_attempts,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_jobs_dedupe_key")
            .returning(Job)
        )
        created = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if created is not None:
            return created

        existing = await self._session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing is None:
            raise RuntimeError("Job upsert returned no row")
        if revive_terminal and existing.status in {
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
        }:
            existing.status = JobStatus.QUEUED.value
            existing.payload = payload
            existing.attempt_count = 0
            existing.max_attempts = max_attempts
            existing.available_at = now
            existing.claim_token = None
            existing.claimed_at = None
            existing.lease_expires_at = None
            existing.heartbeat_at = None
            existing.last_error_code = None
            existing.last_error_detail = None
            existing.completed_at = None
            existing.updated_at = now
            await self._session.flush()
        return existing

    async def claim_next(self, *, job_type: str, lease_seconds: float) -> JobLease | None:
        now = datetime.now(UTC)
        await self._expire_exhausted_leases(job_type=job_type, now=now)
        claimable = or_(
            and_(
                Job.status == JobStatus.QUEUED.value,
                Job.available_at <= now,
            ),
            and_(
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= now,
            ),
        )
        job = await self._session.scalar(
            select(Job)
            .where(
                Job.job_type == job_type,
                claimable,
                Job.attempt_count < Job.max_attempts,
            )
            .order_by(Job.available_at, Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None

        recovered = job.status == JobStatus.RUNNING.value
        token = uuid4()
        job.status = JobStatus.RUNNING.value
        job.attempt_count += 1
        job.claim_token = token
        job.claimed_at = now
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        await self._session.flush()
        return JobLease(
            job_id=job.id,
            claim_token=token,
            job_type=job.job_type,
            payload=dict(job.payload),
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            recovered_from_expired_lease=recovered,
        )

    async def heartbeat(
        self,
        *,
        job_id: UUID,
        claim_token: UUID,
        lease_seconds: float,
    ) -> None:
        job = await self._owned_job(job_id, claim_token)
        now = datetime.now(UTC)
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.updated_at = now
        await self._session.flush()

    async def lease_is_owned(self, *, job_id: UUID, claim_token: UUID) -> bool:
        now = datetime.now(UTC)
        owned = await self._session.scalar(
            select(Job.id).where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING.value,
                Job.claim_token == claim_token,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at > now,
            )
        )
        return owned is not None

    async def complete(self, *, job_id: UUID, claim_token: UUID) -> Job:
        job = await self._owned_job(job_id, claim_token)
        now = datetime.now(UTC)
        job.status = JobStatus.COMPLETED.value
        job.claim_token = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        job.completed_at = now
        await self._session.flush()
        return job

    async def retry(
        self,
        *,
        job_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_detail: str,
        delay_seconds: float,
    ) -> Job:
        job = await self._owned_job(job_id, claim_token)
        now = datetime.now(UTC)
        job.last_error_code = error_code
        job.last_error_detail = error_detail
        job.claim_token = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        if job.attempt_count >= job.max_attempts:
            job.status = JobStatus.FAILED.value
            job.completed_at = now
        else:
            job.status = JobStatus.QUEUED.value
            job.available_at = now + timedelta(seconds=delay_seconds)
            job.completed_at = None
        await self._session.flush()
        return job

    async def fail(
        self,
        *,
        job_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_detail: str,
    ) -> Job:
        job = await self._owned_job(job_id, claim_token)
        now = datetime.now(UTC)
        job.status = JobStatus.FAILED.value
        job.last_error_code = error_code
        job.last_error_detail = error_detail
        job.claim_token = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        job.completed_at = now
        await self._session.flush()
        return job

    async def cancel_owned(self, *, job_id: UUID, claim_token: UUID) -> Job:
        job = await self._owned_job(job_id, claim_token)
        now = datetime.now(UTC)
        job.status = JobStatus.CANCELLED.value
        job.claim_token = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        job.completed_at = now
        await self._session.flush()
        return job

    async def cancel_if_queued(self, *, dedupe_key: str) -> Job | None:
        job = await self._session.scalar(
            select(Job).where(Job.dedupe_key == dedupe_key).with_for_update()
        )
        if job is None:
            return None
        if job.status == JobStatus.QUEUED.value:
            now = datetime.now(UTC)
            job.status = JobStatus.CANCELLED.value
            job.updated_at = now
            job.completed_at = now
            await self._session.flush()
        return job

    async def get(self, job_id: UUID) -> Job | None:
        return await self._session.get(Job, job_id)

    async def get_by_dedupe_key(self, dedupe_key: str) -> Job | None:
        return await self._session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))

    async def _owned_job(self, job_id: UUID, claim_token: UUID) -> Job:
        now = datetime.now(UTC)
        job = await self._session.scalar(
            select(Job)
            .where(
                Job.id == job_id,
                Job.status == JobStatus.RUNNING.value,
                Job.claim_token == claim_token,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at > now,
            )
            .with_for_update()
        )
        if job is None:
            raise JobLeaseLost(f"Job {job_id} lease is no longer owned by this worker")
        return job

    async def _expire_exhausted_leases(self, *, job_type: str, now: datetime) -> None:
        await self._session.execute(
            update(Job)
            .where(
                Job.job_type == job_type,
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= now,
                Job.attempt_count >= Job.max_attempts,
            )
            .values(
                status=JobStatus.FAILED.value,
                claim_token=None,
                lease_expires_at=None,
                heartbeat_at=now,
                last_error_code="WORKER_LEASE_EXPIRED",
                last_error_detail="Worker lease expired after the final permitted attempt.",
                updated_at=now,
                completed_at=now,
            )
        )
