from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_delegation_runtime import _plan, _principal, _runtime, _scope, _SearchExecutor, _step

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_control import DelegationOperatorControlService
from bukmatika.ai.delegation_control_domain import (
    DelegationConsentAction,
    DelegationConsentRequest,
)
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationProposalRequest,
    DelegationResponse,
    DelegationStatus,
)
from bukmatika.ai.delegation_jobs import (
    DELEGATION_JOB_TYPE,
    DelegationJobWorker,
    delegation_job_dedupe_key,
)
from bukmatika.ai.domain import CapabilityName
from bukmatika.config import Settings
from bukmatika.persistence.delegation_models import AIDelegation
from bukmatika.persistence.jobs import Job, JobStatus
from bukmatika.persistence.models import Principal


def _settings() -> Settings:
    return Settings(
        delegation_worker_enabled=False,
        delegation_job_lease_seconds=120,
        delegation_job_heartbeat_seconds=20,
        delegation_job_max_attempts=3,
        delegation_retry_seconds=1,
    )


async def _approved_level2_delegation(
    session: AsyncSession,
    *,
    suffix: str,
) -> tuple[
    DelegationControlService,
    DelegationOperatorControlService,
    Principal,
    DelegationResponse,
]:
    principal = await _principal(session, suffix)
    plan = await _plan(
        session,
        principal_id=principal.id,
        step=_step(CapabilityName.RESEARCH_SEARCH),
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    operator = DelegationOperatorControlService(
        settings=_settings(),
        session_scope_factory=_scope(session),
        delegation_service=control,
    )
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=DelegationProposalRequest(
            step_ids=["research"],
            max_runtime_seconds=300,
            max_retries_per_step=0,
            max_total_attempts=1,
        ),
    )
    await control.decide(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.APPROVED),
    )
    await operator.decide_consent(
        principal_id=principal.id,
        request=DelegationConsentRequest(action=DelegationConsentAction.GRANT),
    )
    return control, operator, principal, proposed


async def test_start_atomically_enqueues_one_idempotent_durable_job(session: AsyncSession) -> None:
    _, operator, principal, proposed = await _approved_level2_delegation(
        session,
        suffix="dispatch-enqueue",
    )

    first = await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    second = await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )

    assert first.status is DelegationStatus.RUNNING
    assert second.status is DelegationStatus.RUNNING
    assert second.started_at == first.started_at
    jobs = (
        await session.scalars(
            select(Job).where(Job.dedupe_key == delegation_job_dedupe_key(proposed.delegation_id))
        )
    ).all()
    assert len(jobs) == 1
    assert jobs[0].job_type == DELEGATION_JOB_TYPE
    assert jobs[0].status == JobStatus.QUEUED.value
    assert jobs[0].payload == {
        "principal_id": str(principal.id),
        "delegation_id": str(proposed.delegation_id),
    }


async def test_worker_executes_queued_delegation_to_completion(session: AsyncSession) -> None:
    control, operator, principal, proposed = await _approved_level2_delegation(
        session,
        suffix="dispatch-complete",
    )
    await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    executor = _SearchExecutor()
    runtime = _runtime(session, control=control, executor=executor)
    worker = DelegationJobWorker(
        _settings(),
        runtime_service=runtime,
        control_service=control,
        session_scope_factory=_scope(session),
    )

    assert await worker.run_once() is True

    assert executor.calls == 1
    delegation = await control.get(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert delegation.status is DelegationStatus.COMPLETED
    job = await session.scalar(
        select(Job).where(Job.dedupe_key == delegation_job_dedupe_key(proposed.delegation_id))
    )
    assert job is not None
    assert job.status == JobStatus.COMPLETED.value
    assert job.claim_token is None


async def test_worker_acknowledges_stop_and_cancels_dispatch_job(session: AsyncSession) -> None:
    control, operator, principal, proposed = await _approved_level2_delegation(
        session,
        suffix="dispatch-stop",
    )
    await operator.start(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    stopped = await control.request_stop(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert stopped.status is DelegationStatus.STOP_REQUESTED

    worker = DelegationJobWorker(
        _settings(),
        runtime_service=_runtime(session, control=control, executor=_SearchExecutor()),
        control_service=control,
        session_scope_factory=_scope(session),
    )
    assert await worker.run_once() is True

    delegation = await control.get(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
    )
    assert delegation.status is DelegationStatus.STOPPED
    job = await session.scalar(
        select(Job).where(Job.dedupe_key == delegation_job_dedupe_key(proposed.delegation_id))
    )
    assert job is not None
    assert job.status == JobStatus.CANCELLED.value


async def test_failed_enqueue_rolls_back_running_transition(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control, _, principal, proposed = await _approved_level2_delegation(
        session,
        suffix="dispatch-rollback",
    )

    @asynccontextmanager
    async def transactional_scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    operator = DelegationOperatorControlService(
        settings=_settings(),
        session_scope_factory=transactional_scope,
        delegation_service=control,
    )

    async def fail_enqueue(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("synthetic enqueue failure")

    monkeypatch.setattr(
        "bukmatika.ai.delegation_control.enqueue_delegation_job_in_session",
        fail_enqueue,
    )

    with pytest.raises(RuntimeError, match="synthetic enqueue failure"):
        await operator.start(
            principal_id=principal.id,
            delegation_id=proposed.delegation_id,
        )

    session.expire_all()
    delegation = await session.get(AIDelegation, proposed.delegation_id)
    assert delegation is not None
    assert delegation.status == DelegationStatus.APPROVED.value
    assert delegation.started_at is None
    assert await session.scalar(
        select(func.count(Job.id)).where(
            Job.dedupe_key == delegation_job_dedupe_key(proposed.delegation_id)
        )
    ) == 0
