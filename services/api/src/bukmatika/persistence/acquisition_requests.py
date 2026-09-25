from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.acquisition_request_models import AcquisitionPolicy, AcquisitionRequest
from bukmatika.persistence.models import Acquisition, Asset


class AcquisitionRequestRepository:
    """Persistence authority for principal acquisition intent and approval policy."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def policy(self, principal_id: UUID) -> AcquisitionPolicy | None:
        return await self._session.get(AcquisitionPolicy, principal_id)

    async def set_policy(self, *, principal_id: UUID, approval_mode: str) -> AcquisitionPolicy:
        policy = await self._session.get(AcquisitionPolicy, principal_id)
        if policy is None:
            policy = AcquisitionPolicy(principal_id=principal_id, approval_mode=approval_mode)
            self._session.add(policy)
        else:
            policy.approval_mode = approval_mode
        await self._session.flush()
        return policy

    async def asset(self, asset_id: UUID) -> Asset | None:
        return await self._session.get(Asset, asset_id)

    async def lock_asset(self, asset_id: UUID) -> Asset | None:
        return await self._session.scalar(
            select(Asset).where(Asset.id == asset_id).with_for_update(of=Asset)
        )

    async def request_for_asset(
        self,
        *,
        principal_id: UUID,
        asset_id: UUID,
        for_update: bool = False,
    ) -> AcquisitionRequest | None:
        statement = select(AcquisitionRequest).where(
            AcquisitionRequest.principal_id == principal_id,
            AcquisitionRequest.asset_id == asset_id,
        )
        if for_update:
            statement = statement.with_for_update(of=AcquisitionRequest)
        return await self._session.scalar(statement)

    async def request_by_id(
        self,
        *,
        principal_id: UUID,
        request_id: UUID,
        for_update: bool = False,
    ) -> AcquisitionRequest | None:
        statement = select(AcquisitionRequest).where(
            AcquisitionRequest.id == request_id,
            AcquisitionRequest.principal_id == principal_id,
        )
        if for_update:
            statement = statement.with_for_update(of=AcquisitionRequest)
        return await self._session.scalar(statement)

    async def create_or_reactivate(
        self,
        *,
        principal_id: UUID,
        asset_id: UUID,
        approval_mode: str,
        reactivate_terminal: bool = False,
    ) -> AcquisitionRequest:
        request = await self.request_for_asset(
            principal_id=principal_id,
            asset_id=asset_id,
            for_update=True,
        )
        if request is None:
            request = AcquisitionRequest(
                principal_id=principal_id,
                asset_id=asset_id,
                approval_mode=approval_mode,
            )
            self._session.add(request)
        elif request.cancelled_at is not None or reactivate_terminal:
            request.approval_mode = approval_mode
            request.approved_at = None
            request.cancelled_at = None
            request.acquisition_id = None
        elif request.approved_at is None:
            request.approval_mode = approval_mode
        await self._session.flush()
        return request

    async def approve(
        self,
        request: AcquisitionRequest,
        *,
        acquisition_id: UUID | None = None,
    ) -> AcquisitionRequest:
        if request.approved_at is None:
            request.approved_at = datetime.now(UTC)
        request.cancelled_at = None
        if acquisition_id is not None:
            request.acquisition_id = acquisition_id
        await self._session.flush()
        return request

    async def link_acquisition(
        self,
        request: AcquisitionRequest,
        acquisition_id: UUID,
    ) -> AcquisitionRequest:
        request.acquisition_id = acquisition_id
        await self._session.flush()
        return request

    async def cancel(self, request: AcquisitionRequest) -> AcquisitionRequest:
        if request.cancelled_at is None:
            request.cancelled_at = datetime.now(UTC)
        await self._session.flush()
        return request

    async def active_approved_count(self, *, asset_id: UUID) -> int:
        count = await self._session.scalar(
            select(func.count(AcquisitionRequest.id)).where(
                AcquisitionRequest.asset_id == asset_id,
                AcquisitionRequest.approved_at.is_not(None),
                AcquisitionRequest.cancelled_at.is_(None),
            )
        )
        return int(count or 0)

    async def acquisition(self, acquisition_id: UUID) -> Acquisition | None:
        return await self._session.get(Acquisition, acquisition_id)

    async def acquisition_for_asset(self, asset_id: UUID) -> Acquisition | None:
        return await self._session.scalar(
            select(Acquisition).where(Acquisition.asset_id == asset_id)
        )
