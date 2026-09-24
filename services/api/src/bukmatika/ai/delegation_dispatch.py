from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.config import Settings
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.jobs import Job, JobRepository

DELEGATION_JOB_TYPE = "ai_delegation"


def delegation_job_dedupe_key(delegation_id: UUID) -> str:
    return f"ai-delegation:{delegation_id}"


async def enqueue_delegation_job_in_session(
    database_session: AsyncSession,
    *,
    settings: Settings,
    principal_id: UUID,
    delegation_id: UUID,
) -> Job:
    job = await JobRepository(database_session).enqueue(
        job_type=DELEGATION_JOB_TYPE,
        payload={
            "principal_id": str(principal_id),
            "delegation_id": str(delegation_id),
        },
        dedupe_key=delegation_job_dedupe_key(delegation_id),
        max_attempts=settings.delegation_job_max_attempts,
    )
    await InteractionEventRepository(database_session).record(
        SemanticEventType.AI_DELEGATION_DISPATCH_QUEUED,
        principal_id=principal_id,
        entity_type="ai_delegation",
        entity_id=delegation_id,
        context={
            "delegation_id": str(delegation_id),
            "job_id": str(job.id),
            "job_status": job.status,
        },
    )
    return job
