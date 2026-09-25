from sqlalchemy.ext.asyncio import AsyncSession
from test_acquisition_jobs import _seed_open_asset
from test_principal_acquisition_requests import _principal, _service

from bukmatika.acquisition.domain import AcquisitionRequestStatus, AcquisitionStatus


async def test_reapproved_cancelled_request_requeues_canonical_transfer(
    session: AsyncSession,
    tmp_path,
) -> None:
    principal = await _principal(session, "requeue-coherence")
    asset = await _seed_open_asset(session, "799")
    service, queue = await _service(session, tmp_path)

    pending = await service.request_asset(principal_id=principal.id, asset_id=asset.id)
    approved = await service.approve(
        principal_id=principal.id,
        request_id=pending.request_id,
    )
    assert approved.acquisition_id is not None
    cancelled = await service.cancel(
        principal_id=principal.id,
        request_id=pending.request_id,
    )
    assert cancelled.status is AcquisitionRequestStatus.CANCELLED
    assert (await queue.get(approved.acquisition_id)).status is AcquisitionStatus.CANCELLED

    restarted = await service.request_asset(principal_id=principal.id, asset_id=asset.id)
    assert restarted.status is AcquisitionRequestStatus.PENDING_APPROVAL
    reapproved = await service.approve(
        principal_id=principal.id,
        request_id=restarted.request_id,
    )

    assert reapproved.status is AcquisitionRequestStatus.ACTIVE
    assert reapproved.acquisition_id == approved.acquisition_id
    assert (await queue.get(approved.acquisition_id)).status is AcquisitionStatus.QUEUED
