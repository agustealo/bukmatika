from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_acquisition_jobs import _scope, _seed_open_asset, _settings

from bukmatika.acquisition.domain import (
    AcquisitionApprovalMode,
    AcquisitionPolicyUpdate,
    AcquisitionRequestStatus,
    AcquisitionStatus,
)
from bukmatika.acquisition.jobs import AcquisitionQueueService
from bukmatika.acquisition.requests import (
    PrincipalAcquisitionRequestNotFound,
    PrincipalAcquisitionService,
)
from bukmatika.persistence.acquisition_request_models import AcquisitionRequest
from bukmatika.persistence.events import SemanticEventType
from bukmatika.persistence.jobs import JobRepository, JobStatus
from bukmatika.persistence.models import Acquisition, InteractionEvent, Principal


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"acquisition-user-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def _service(
    session: AsyncSession,
    tmp_path,  # type: ignore[no-untyped-def]
) -> tuple[PrincipalAcquisitionService, AcquisitionQueueService]:
    settings = _settings(tmp_path)
    queue = AcquisitionQueueService(settings, session_scope_factory=_scope(session))
    service = PrincipalAcquisitionService(
        settings,
        queue=queue,
        session_scope_factory=_scope(session),
    )
    return service, queue


async def _global_acquisition(session: AsyncSession, asset_id: UUID) -> Acquisition | None:
    return await session.scalar(select(Acquisition).where(Acquisition.asset_id == asset_id))


async def _event_count(
    session: AsyncSession,
    *,
    principal_id: UUID,
    asset_id: UUID,
    event_type: SemanticEventType,
) -> int:
    count = await session.scalar(
        select(func.count(InteractionEvent.id)).where(
            InteractionEvent.principal_id == principal_id,
            InteractionEvent.entity_type == "asset",
            InteractionEvent.entity_id == asset_id,
            InteractionEvent.event_type == event_type.value,
        )
    )
    return int(count or 0)


async def test_default_policy_requires_approval_before_global_enqueue(
    session: AsyncSession,
    tmp_path,
) -> None:
    principal = await _principal(session, "ask")
    asset = await _seed_open_asset(session, "701")
    service, _ = await _service(session, tmp_path)

    policy = await service.policy(principal_id=principal.id)
    request = await service.request_asset(principal_id=principal.id, asset_id=asset.id)

    assert policy.approval_mode is AcquisitionApprovalMode.ALWAYS_ASK
    assert request.status is AcquisitionRequestStatus.PENDING_APPROVAL
    assert request.acquisition_id is None
    assert await _global_acquisition(session, asset.id) is None


async def test_auto_eligible_explicit_request_enqueues_once(
    session: AsyncSession,
    tmp_path,
) -> None:
    principal = await _principal(session, "auto")
    asset = await _seed_open_asset(session, "702")
    service, _ = await _service(session, tmp_path)
    await service.update_policy(
        principal_id=principal.id,
        update=AcquisitionPolicyUpdate(approval_mode=AcquisitionApprovalMode.AUTO_ELIGIBLE),
    )

    first = await service.request_asset(principal_id=principal.id, asset_id=asset.id)
    second = await service.request_asset(principal_id=principal.id, asset_id=asset.id)

    assert first.request_id == second.request_id
    assert first.acquisition_id is not None
    assert first.acquisition_id == second.acquisition_id
    assert first.status is AcquisitionRequestStatus.ACTIVE
    acquisition = await _global_acquisition(session, asset.id)
    assert acquisition is not None
    job = await JobRepository(session).get_by_dedupe_key(f"acquisition:{acquisition.id}")
    assert job is not None
    assert job.status == JobStatus.QUEUED.value
    rows = (
        await session.scalars(
            select(AcquisitionRequest).where(AcquisitionRequest.asset_id == asset.id)
        )
    ).all()
    assert len(rows) == 1
    assert (
        await _event_count(
            session,
            principal_id=principal.id,
            asset_id=asset.id,
            event_type=SemanticEventType.ACQUISITION_REQUEST_CREATED,
        )
        == 1
    )
    assert (
        await _event_count(
            session,
            principal_id=principal.id,
            asset_id=asset.id,
            event_type=SemanticEventType.ACQUISITION_REQUEST_APPROVED,
        )
        == 1
    )


async def test_two_principals_share_transfer_and_last_cancel_stops_it(
    session: AsyncSession,
    tmp_path,
) -> None:
    first_principal = await _principal(session, "shared-a")
    second_principal = await _principal(session, "shared-b")
    asset = await _seed_open_asset(session, "703")
    service, queue = await _service(session, tmp_path)
    for principal in (first_principal, second_principal):
        await service.update_policy(
            principal_id=principal.id,
            update=AcquisitionPolicyUpdate(
                approval_mode=AcquisitionApprovalMode.AUTO_ELIGIBLE
            ),
        )

    first = await service.request_asset(principal_id=first_principal.id, asset_id=asset.id)
    second = await service.request_asset(principal_id=second_principal.id, asset_id=asset.id)
    assert first.acquisition_id is not None
    assert first.acquisition_id == second.acquisition_id

    cancelled_first = await service.cancel(
        principal_id=first_principal.id,
        request_id=first.request_id,
    )
    shared_after_first_cancel = await queue.get(first.acquisition_id)
    assert cancelled_first.status is AcquisitionRequestStatus.CANCELLED
    assert shared_after_first_cancel.status is AcquisitionStatus.QUEUED

    cancelled_second = await service.cancel(
        principal_id=second_principal.id,
        request_id=second.request_id,
    )
    shared_after_last_cancel = await queue.get(first.acquisition_id)
    assert cancelled_second.status is AcquisitionRequestStatus.CANCELLED
    assert shared_after_last_cancel.status is AcquisitionStatus.CANCELLED


async def test_request_ids_are_principal_private(
    session: AsyncSession,
    tmp_path,
) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    asset = await _seed_open_asset(session, "704")
    service, _ = await _service(session, tmp_path)
    request = await service.request_asset(principal_id=owner.id, asset_id=asset.id)

    with pytest.raises(PrincipalAcquisitionRequestNotFound):
        await service.get(principal_id=other.id, request_id=request.request_id)
    with pytest.raises(PrincipalAcquisitionRequestNotFound):
        await service.approve(principal_id=other.id, request_id=request.request_id)
    with pytest.raises(PrincipalAcquisitionRequestNotFound):
        await service.cancel(principal_id=other.id, request_id=request.request_id)


async def test_cancelled_request_can_be_explicitly_requested_again(
    session: AsyncSession,
    tmp_path,
) -> None:
    principal = await _principal(session, "retry")
    asset = await _seed_open_asset(session, "705")
    service, _ = await _service(session, tmp_path)

    original = await service.request_asset(principal_id=principal.id, asset_id=asset.id)
    cancelled = await service.cancel(
        principal_id=principal.id,
        request_id=original.request_id,
    )
    restarted = await service.request_asset(principal_id=principal.id, asset_id=asset.id)

    assert cancelled.status is AcquisitionRequestStatus.CANCELLED
    assert restarted.request_id == original.request_id
    assert restarted.status is AcquisitionRequestStatus.PENDING_APPROVAL
    assert restarted.cancelled_at is None
    assert restarted.acquisition_id is None
    assert (
        await _event_count(
            session,
            principal_id=principal.id,
            asset_id=asset.id,
            event_type=SemanticEventType.ACQUISITION_REQUEST_CREATED,
        )
        == 2
    )


async def test_manual_approval_enqueues_shared_transfer(
    session: AsyncSession,
    tmp_path,
) -> None:
    principal = await _principal(session, "manual")
    asset = await _seed_open_asset(session, "706")
    service, _ = await _service(session, tmp_path)
    pending = await service.request_asset(principal_id=principal.id, asset_id=asset.id)

    approved = await service.approve(
        principal_id=principal.id,
        request_id=pending.request_id,
    )

    assert approved.status is AcquisitionRequestStatus.ACTIVE
    assert approved.approved_at is not None
    assert approved.acquisition_id is not None
