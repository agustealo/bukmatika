from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import (
    AcquisitionApprovalMode,
    AcquisitionPolicyResponse,
    AcquisitionPolicyUpdate,
    AcquisitionRequestResponse,
    AcquisitionRequestStatus,
    AcquisitionStatus,
)
from bukmatika.acquisition.jobs import AcquisitionQueueService
from bukmatika.acquisition.service import AssetNotFound
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition_request_models import AcquisitionRequest
from bukmatika.persistence.acquisition_requests import AcquisitionRequestRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import Acquisition, Asset

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PrincipalAcquisitionRequestNotFound(LookupError):
    pass


class PrincipalAcquisitionRequestConflict(RuntimeError):
    pass


class PrincipalAcquisitionService:
    """Principal intent/approval authority layered over the shared exact-asset transfer queue."""

    def __init__(
        self,
        settings: Settings,
        *,
        queue: AcquisitionQueueService | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory
        self._queue = queue or AcquisitionQueueService(
            settings,
            session_scope_factory=session_scope_factory,
        )

    async def policy(self, *, principal_id: UUID) -> AcquisitionPolicyResponse:
        async with self._session_scope() as database_session:
            stored = await AcquisitionRequestRepository(database_session).policy(principal_id)
            mode = (
                AcquisitionApprovalMode(stored.approval_mode)
                if stored is not None
                else AcquisitionApprovalMode.ALWAYS_ASK
            )
            return AcquisitionPolicyResponse(approval_mode=mode)

    async def update_policy(
        self,
        *,
        principal_id: UUID,
        update: AcquisitionPolicyUpdate,
    ) -> AcquisitionPolicyResponse:
        async with self._session_scope() as database_session:
            policy = await AcquisitionRequestRepository(database_session).set_policy(
                principal_id=principal_id,
                approval_mode=update.approval_mode.value,
            )
            return AcquisitionPolicyResponse(
                approval_mode=AcquisitionApprovalMode(policy.approval_mode)
            )

    async def request_asset(
        self,
        *,
        principal_id: UUID,
        asset_id: UUID,
    ) -> AcquisitionRequestResponse:
        async with self._session_scope() as database_session:
            repository = AcquisitionRequestRepository(database_session)
            asset = await repository.lock_asset(asset_id)
            if asset is None:
                raise AssetNotFound(f"Asset {asset_id} does not exist")
            policy = await repository.policy(principal_id)
            mode = (
                AcquisitionApprovalMode(policy.approval_mode)
                if policy is not None
                else AcquisitionApprovalMode.ALWAYS_ASK
            )
            existing = await repository.request_for_asset(
                principal_id=principal_id,
                asset_id=asset.id,
            )
            created_or_reactivated = existing is None or existing.cancelled_at is not None
            request = await repository.create_or_reactivate(
                principal_id=principal_id,
                asset_id=asset.id,
                approval_mode=mode.value,
            )
            if created_or_reactivated:
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.ACQUISITION_REQUEST_CREATED,
                    principal_id=principal_id,
                    entity_type="asset",
                    entity_id=asset.id,
                    context={
                        "request_id": str(request.id),
                        "approval_mode": mode.value,
                    },
                )

            newly_approved = request.approved_at is None
            if asset.stored_object_id is not None:
                acquisition = await repository.acquisition_for_asset(asset.id)
                request = await repository.approve(
                    request,
                    acquisition_id=acquisition.id if acquisition is not None else None,
                )
                if newly_approved:
                    await self._record_approval(
                        database_session,
                        principal_id=principal_id,
                        request=request,
                        automatic=True,
                        already_stored=True,
                    )
                return _request_response(request, asset=asset, acquisition=acquisition)

            if mode is AcquisitionApprovalMode.AUTO_ELIGIBLE:
                request = await repository.approve(request)
                queued = await self._queue.enqueue_in_session(
                    database_session,
                    asset.id,
                    principal_id=principal_id,
                )
                request = await repository.link_acquisition(request, queued.acquisition_id)
                if newly_approved:
                    await self._record_approval(
                        database_session,
                        principal_id=principal_id,
                        request=request,
                        automatic=True,
                        already_stored=False,
                    )
                acquisition = await repository.acquisition(queued.acquisition_id)
                return _request_response(request, asset=asset, acquisition=acquisition)

            acquisition = (
                await repository.acquisition(request.acquisition_id)
                if request.acquisition_id is not None
                else None
            )
            return _request_response(request, asset=asset, acquisition=acquisition)

    async def approve(
        self,
        *,
        principal_id: UUID,
        request_id: UUID,
    ) -> AcquisitionRequestResponse:
        async with self._session_scope() as database_session:
            repository = AcquisitionRequestRepository(database_session)
            request = await repository.request_by_id(
                principal_id=principal_id,
                request_id=request_id,
            )
            if request is None:
                raise PrincipalAcquisitionRequestNotFound("Acquisition request does not exist")
            asset = await repository.lock_asset(request.asset_id)
            if asset is None:
                raise AssetNotFound(f"Asset {request.asset_id} does not exist")
            request = await repository.request_by_id(
                principal_id=principal_id,
                request_id=request_id,
                for_update=True,
            )
            if request is None:
                raise PrincipalAcquisitionRequestNotFound("Acquisition request does not exist")
            if request.cancelled_at is not None:
                raise PrincipalAcquisitionRequestConflict(
                    "Cancelled request must be re-requested before approval"
                )

            newly_approved = request.approved_at is None
            request = await repository.approve(request)
            acquisition: Acquisition | None = None
            if asset.stored_object_id is not None:
                acquisition = await repository.acquisition_for_asset(asset.id)
                if acquisition is not None:
                    request = await repository.link_acquisition(request, acquisition.id)
            else:
                queued = await self._queue.enqueue_in_session(
                    database_session,
                    asset.id,
                    principal_id=principal_id,
                )
                request = await repository.link_acquisition(request, queued.acquisition_id)
                acquisition = await repository.acquisition(queued.acquisition_id)

            if newly_approved:
                await self._record_approval(
                    database_session,
                    principal_id=principal_id,
                    request=request,
                    automatic=False,
                    already_stored=asset.stored_object_id is not None,
                )
            return _request_response(request, asset=asset, acquisition=acquisition)

    async def get(
        self,
        *,
        principal_id: UUID,
        request_id: UUID,
    ) -> AcquisitionRequestResponse:
        async with self._session_scope() as database_session:
            repository = AcquisitionRequestRepository(database_session)
            request = await repository.request_by_id(
                principal_id=principal_id,
                request_id=request_id,
            )
            if request is None:
                raise PrincipalAcquisitionRequestNotFound("Acquisition request does not exist")
            asset = await repository.asset(request.asset_id)
            if asset is None:
                raise AssetNotFound(f"Asset {request.asset_id} does not exist")
            acquisition = (
                await repository.acquisition(request.acquisition_id)
                if request.acquisition_id is not None
                else None
            )
            return _request_response(request, asset=asset, acquisition=acquisition)

    async def cancel(
        self,
        *,
        principal_id: UUID,
        request_id: UUID,
    ) -> AcquisitionRequestResponse:
        async with self._session_scope() as database_session:
            repository = AcquisitionRequestRepository(database_session)
            request = await repository.request_by_id(
                principal_id=principal_id,
                request_id=request_id,
            )
            if request is None:
                raise PrincipalAcquisitionRequestNotFound("Acquisition request does not exist")
            asset = await repository.lock_asset(request.asset_id)
            if asset is None:
                raise AssetNotFound(f"Asset {request.asset_id} does not exist")
            request = await repository.request_by_id(
                principal_id=principal_id,
                request_id=request_id,
                for_update=True,
            )
            if request is None:
                raise PrincipalAcquisitionRequestNotFound("Acquisition request does not exist")
            if request.cancelled_at is not None:
                existing_acquisition = (
                    await repository.acquisition(request.acquisition_id)
                    if request.acquisition_id is not None
                    else None
                )
                return _request_response(
                    request,
                    asset=asset,
                    acquisition=existing_acquisition,
                )

            was_approved = request.approved_at is not None
            acquisition_id = request.acquisition_id
            request = await repository.cancel(request)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACQUISITION_REQUEST_CANCELLED,
                principal_id=principal_id,
                entity_type="asset",
                entity_id=asset.id,
                context={
                    "request_id": str(request.id),
                    "acquisition_id": str(acquisition_id) if acquisition_id is not None else None,
                },
            )

            acquisition: Acquisition | None = None
            if was_approved and acquisition_id is not None:
                other_active_requests = await repository.active_approved_count(asset_id=asset.id)
                if other_active_requests == 0:
                    await self._queue.cancel_in_session(
                        database_session,
                        acquisition_id,
                        principal_id=principal_id,
                    )
                acquisition = await repository.acquisition(acquisition_id)
            return _request_response(request, asset=asset, acquisition=acquisition)

    async def request_for_asset(
        self,
        *,
        database_session: AsyncSession,
        principal_id: UUID,
        asset: Asset,
    ) -> AcquisitionRequestResponse | None:
        repository = AcquisitionRequestRepository(database_session)
        request = await repository.request_for_asset(
            principal_id=principal_id,
            asset_id=asset.id,
        )
        if request is None:
            return None
        acquisition = (
            await repository.acquisition(request.acquisition_id)
            if request.acquisition_id is not None
            else None
        )
        return _request_response(request, asset=asset, acquisition=acquisition)

    @staticmethod
    async def _record_approval(
        database_session: AsyncSession,
        *,
        principal_id: UUID,
        request: AcquisitionRequest,
        automatic: bool,
        already_stored: bool,
    ) -> None:
        await InteractionEventRepository(database_session).record(
            SemanticEventType.ACQUISITION_REQUEST_APPROVED,
            principal_id=principal_id,
            entity_type="asset",
            entity_id=request.asset_id,
            context={
                "request_id": str(request.id),
                "acquisition_id": (
                    str(request.acquisition_id) if request.acquisition_id is not None else None
                ),
                "automatic": automatic,
                "already_stored": already_stored,
            },
        )


def _request_response(
    request: AcquisitionRequest,
    *,
    asset: Asset,
    acquisition: Acquisition | None,
) -> AcquisitionRequestResponse:
    acquisition_status = (
        AcquisitionStatus(acquisition.status) if acquisition is not None else None
    )
    if request.cancelled_at is not None:
        request_status = AcquisitionRequestStatus.CANCELLED
    elif asset.stored_object_id is not None or acquisition_status is AcquisitionStatus.STORED:
        request_status = AcquisitionRequestStatus.STORED
    elif acquisition_status is AcquisitionStatus.QUARANTINED:
        request_status = AcquisitionRequestStatus.QUARANTINED
    elif acquisition_status is AcquisitionStatus.FAILED:
        request_status = AcquisitionRequestStatus.FAILED
    elif request.approved_at is not None:
        request_status = AcquisitionRequestStatus.ACTIVE
    else:
        request_status = AcquisitionRequestStatus.PENDING_APPROVAL

    return AcquisitionRequestResponse(
        request_id=request.id,
        asset_id=request.asset_id,
        acquisition_id=request.acquisition_id,
        approval_mode=AcquisitionApprovalMode(request.approval_mode),
        status=request_status,
        approved_at=request.approved_at,
        cancelled_at=request.cancelled_at,
        acquisition_status=acquisition_status,
    )


__all__ = [
    "PrincipalAcquisitionRequestConflict",
    "PrincipalAcquisitionRequestNotFound",
    "PrincipalAcquisitionService",
]
