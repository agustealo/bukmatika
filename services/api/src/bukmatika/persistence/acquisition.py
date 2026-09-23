from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    RightsDecision,
    RightsDecisionEvidence,
    RightsEvidenceRecord,
    RightsEvidenceSubject,
    StoredObject,
)


class AcquisitionStateConflict(RuntimeError):
    pass


class AcquisitionRepository:
    """Durable authority for exact-asset acquisition state and stored content."""

    _active_states = ("resolving", "downloading", "verifying")

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_asset(self, asset_id: UUID) -> Asset | None:
        # Asset-scoped acquisition/rights mutation must share one lock authority.
        # Portable byte import also locks this row before canonical retention, so
        # a newly-recorded rights decision cannot race that retention boundary.
        return await self._session.scalar(
            select(Asset).where(Asset.id == asset_id).with_for_update()
        )

    async def get_acquisition(self, acquisition_id: UUID) -> Acquisition | None:
        return await self._session.get(Acquisition, acquisition_id)

    async def get_acquisition_for_asset(self, asset_id: UUID) -> Acquisition | None:
        return await self._session.scalar(
            select(Acquisition).where(Acquisition.asset_id == asset_id)
        )

    async def get_stored_object(self, stored_object_id: UUID) -> StoredObject | None:
        return await self._session.get(StoredObject, stored_object_id)

    async def rights_evidence_for_asset(self, asset_id: UUID) -> list[RightsEvidenceRecord]:
        result = await self._session.scalars(
            select(RightsEvidenceRecord)
            .join(
                RightsEvidenceSubject,
                RightsEvidenceSubject.rights_evidence_id == RightsEvidenceRecord.id,
            )
            .where(
                RightsEvidenceSubject.subject_type == "asset",
                RightsEvidenceSubject.subject_id == asset_id,
            )
            .order_by(RightsEvidenceRecord.created_at, RightsEvidenceRecord.id)
        )
        return list(result)

    async def create_or_get_acquisition(self, asset: Asset) -> Acquisition:
        if asset.remote_url is None:
            raise ValueError("Asset has no remote URL")
        statement = (
            insert(Acquisition)
            .values(
                asset_id=asset.id,
                status="queued",
                remote_url=asset.remote_url,
                expected_format=asset.format,
            )
            .on_conflict_do_nothing(constraint="uq_acquisition_asset")
            .returning(Acquisition)
        )
        created = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self._session.scalar(
            select(Acquisition).where(Acquisition.asset_id == asset.id)
        )
        if existing is None:
            raise RuntimeError("Acquisition upsert returned no row")
        return existing

    async def begin_attempt(self, acquisition_id: UUID) -> Acquisition:
        statement = (
            update(Acquisition)
            .where(
                Acquisition.id == acquisition_id,
                Acquisition.status.in_(("queued", "failed", "cancelled")),
            )
            .values(
                status="resolving",
                attempt_count=Acquisition.attempt_count + 1,
                bytes_received=None,
                redirect_count=0,
                sha256=None,
                media_type=None,
                error_code=None,
                error_detail=None,
                cancel_requested=False,
                started_at=func.now(),
                completed_at=None,
                updated_at=func.now(),
            )
            .returning(Acquisition)
        )
        acquisition = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if acquisition is None:
            current = await self._session.get(Acquisition, acquisition_id)
            status = current.status if current is not None else "missing"
            raise AcquisitionStateConflict(
                f"Acquisition {acquisition_id} cannot start from state {status}"
            )
        return acquisition

    async def request_cancel(self, acquisition_id: UUID) -> Acquisition:
        acquisition = await self._session.scalar(
            select(Acquisition).where(Acquisition.id == acquisition_id).with_for_update()
        )
        if acquisition is None:
            raise AcquisitionStateConflict(f"Acquisition {acquisition_id} does not exist")
        if acquisition.status in {"stored", "quarantined"}:
            return acquisition

        acquisition.cancel_requested = True
        acquisition.updated_at = func.now()
        if acquisition.status in {"queued", "failed", "cancelled"}:
            acquisition.status = "cancelled"
            acquisition.error_code = "CANCELLED"
            acquisition.error_detail = "Acquisition was cancelled by the user."
            acquisition.completed_at = func.now()
        await self._session.flush()
        return acquisition

    async def is_cancel_requested(self, acquisition_id: UUID) -> bool:
        value = await self._session.scalar(
            select(Acquisition.cancel_requested).where(Acquisition.id == acquisition_id)
        )
        return bool(value)

    async def recover_expired_attempt(self, asset_id: UUID) -> Acquisition | None:
        acquisition = await self._session.scalar(
            select(Acquisition)
            .where(Acquisition.asset_id == asset_id)
            .with_for_update()
        )
        if acquisition is None or acquisition.status not in self._active_states:
            return acquisition

        acquisition.completed_at = func.now()
        acquisition.updated_at = func.now()
        if acquisition.cancel_requested:
            acquisition.status = "cancelled"
            acquisition.error_code = "CANCELLED"
            acquisition.error_detail = "Cancelled while the previous worker lease expired."
        else:
            acquisition.status = "failed"
            acquisition.error_code = "WORKER_LEASE_EXPIRED"
            acquisition.error_detail = "Previous acquisition worker lease expired."
        await self._session.flush()
        return acquisition

    async def record_rights_decision(
        self,
        *,
        asset_id: UUID,
        rights_state: str,
        permissions: dict[str, bool],
        reason: str,
        evidence_ids: Sequence[UUID],
        jurisdiction: str,
        policy_version: str,
    ) -> RightsDecision:
        # Rights changes and byte retention serialize on the exact same Asset row.
        # Re-locking is harmless when the caller already owns the lock and makes
        # direct repository use fail closed instead of bypassing the authority.
        asset = await self._session.scalar(
            select(Asset).where(Asset.id == asset_id).with_for_update()
        )
        if asset is None:
            raise ValueError(f"Asset {asset_id} does not exist")

        decision = RightsDecision(
            subject_type="asset",
            subject_id=asset_id,
            rights_state=rights_state,
            jurisdiction=jurisdiction,
            policy_version=policy_version,
            permissions=permissions,
            reason=reason,
        )
        self._session.add(decision)
        await self._session.flush()
        for evidence_id in evidence_ids:
            self._session.add(
                RightsDecisionEvidence(
                    rights_decision_id=decision.id,
                    rights_evidence_id=evidence_id,
                )
            )
        await self._session.flush()
        return decision

    async def mark_downloading(
        self,
        acquisition_id: UUID,
        rights_decision_id: UUID,
    ) -> Acquisition:
        return await self._transition(
            acquisition_id,
            from_states=("resolving",),
            status="downloading",
            rights_decision_id=rights_decision_id,
        )

    async def mark_verifying(
        self,
        acquisition_id: UUID,
        *,
        bytes_received: int,
        sha256: str,
        media_type: str | None,
        redirect_count: int,
    ) -> Acquisition:
        return await self._transition(
            acquisition_id,
            from_states=("downloading",),
            status="verifying",
            bytes_received=bytes_received,
            sha256=sha256,
            media_type=media_type,
            redirect_count=redirect_count,
        )

    async def mark_failed(
        self,
        acquisition_id: UUID,
        *,
        error_code: str,
        error_detail: str,
        rights_decision_id: UUID | None = None,
    ) -> Acquisition:
        values: dict[str, object] = {
            "status": "failed",
            "error_code": error_code,
            "error_detail": error_detail,
            "completed_at": func.now(),
            "updated_at": func.now(),
        }
        if rights_decision_id is not None:
            values["rights_decision_id"] = rights_decision_id
        statement = (
            update(Acquisition)
            .where(Acquisition.id == acquisition_id)
            .values(**values)
            .returning(Acquisition)
        )
        acquisition = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if acquisition is None:
            raise AcquisitionStateConflict(f"Acquisition {acquisition_id} does not exist")
        return acquisition

    async def mark_cancelled(self, acquisition_id: UUID) -> Acquisition:
        return await self._transition(
            acquisition_id,
            from_states=("queued", "resolving", "downloading", "verifying", "failed"),
            status="cancelled",
            cancel_requested=True,
            error_code="CANCELLED",
            error_detail="Acquisition was cancelled by the user.",
            completed_at=func.now(),
        )

    async def mark_quarantined(
        self,
        acquisition_id: UUID,
        *,
        error_code: str,
        error_detail: str,
    ) -> Acquisition:
        return await self._transition(
            acquisition_id,
            from_states=("verifying",),
            status="quarantined",
            error_code=error_code,
            error_detail=error_detail,
            completed_at=func.now(),
        )

    async def upsert_stored_object(
        self,
        *,
        sha256: str,
        storage_key: str,
        byte_size: int,
        media_type: str | None,
    ) -> StoredObject:
        statement = (
            insert(StoredObject)
            .values(
                sha256=sha256,
                storage_key=storage_key,
                byte_size=byte_size,
                media_type=media_type,
            )
            .on_conflict_do_nothing(constraint="uq_stored_objects_sha256")
            .returning(StoredObject)
        )
        stored = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if stored is not None:
            return stored
        existing = await self._session.scalar(
            select(StoredObject).where(StoredObject.sha256 == sha256)
        )
        if existing is None:
            raise RuntimeError("Stored object upsert returned no row")
        return existing

    async def mark_stored(
        self,
        acquisition_id: UUID,
        *,
        asset_id: UUID,
        stored_object_id: UUID,
        byte_size: int,
    ) -> Acquisition:
        await self._session.execute(
            update(Asset)
            .where(Asset.id == asset_id)
            .values(
                stored_object_id=stored_object_id,
                byte_size=byte_size,
                updated_at=func.now(),
            )
        )
        return await self._transition(
            acquisition_id,
            from_states=("verifying",),
            status="stored",
            stored_object_id=stored_object_id,
            completed_at=func.now(),
        )

    async def _transition(
        self,
        acquisition_id: UUID,
        *,
        from_states: tuple[str, ...],
        status: str,
        **values: object,
    ) -> Acquisition:
        statement = (
            update(Acquisition)
            .where(
                Acquisition.id == acquisition_id,
                Acquisition.status.in_(from_states),
            )
            .values(status=status, updated_at=func.now(), **values)
            .returning(Acquisition)
        )
        acquisition = (
            await self._session.execute(statement.execution_options(populate_existing=True))
        ).scalar_one_or_none()
        if acquisition is None:
            current = await self._session.get(Acquisition, acquisition_id)
            current_status = current.status if current is not None else "missing"
            raise AcquisitionStateConflict(
                f"Acquisition {acquisition_id} cannot transition from {current_status} to {status}"
            )
        return acquisition
