from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Edition,
    LibraryEntry,
    RightsDecision,
    StoredObject,
)


@dataclass(frozen=True, slots=True)
class BytePortabilityCandidate:
    asset_id: UUID
    asset_format: str
    asset_byte_size: int | None
    stored_object_id: UUID
    storage_key: str
    sha256: str
    byte_size: int
    media_type: str | None
    rights_decision_id: UUID | None
    rights_state: str | None
    permissions: dict[str, bool]


class LibraryBytePortabilityRepository:
    """Read-only projection over canonical ownership, acquisition, storage, and rights."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def export_candidate(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        asset_id: UUID,
    ) -> BytePortabilityCandidate | None:
        row = (
            await self._session.execute(
                select(Asset, StoredObject)
                .join(Edition, Edition.id == Asset.edition_id)
                .join(
                    LibraryEntry,
                    and_(
                        LibraryEntry.work_id == Edition.work_id,
                        or_(
                            LibraryEntry.edition_id.is_(None),
                            LibraryEntry.edition_id == Edition.id,
                        ),
                    ),
                )
                .join(
                    Acquisition,
                    and_(
                        Acquisition.asset_id == Asset.id,
                        Acquisition.status == "stored",
                    ),
                )
                .join(
                    StoredObject,
                    and_(
                        StoredObject.id == Asset.stored_object_id,
                        StoredObject.id == Acquisition.stored_object_id,
                    ),
                )
                .where(
                    LibraryEntry.id == library_entry_id,
                    LibraryEntry.principal_id == principal_id,
                    Asset.id == asset_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None

        asset, stored = row
        rights = await self._session.scalar(
            select(RightsDecision)
            .where(
                RightsDecision.subject_type == "asset",
                RightsDecision.subject_id == asset.id,
            )
            .order_by(RightsDecision.evaluated_at.desc(), RightsDecision.id.desc())
            .limit(1)
        )
        return BytePortabilityCandidate(
            asset_id=asset.id,
            asset_format=asset.format,
            asset_byte_size=asset.byte_size,
            stored_object_id=stored.id,
            storage_key=stored.storage_key,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            media_type=stored.media_type,
            rights_decision_id=rights.id if rights is not None else None,
            rights_state=rights.rights_state if rights is not None else None,
            permissions=dict(rights.permissions) if rights is not None else {},
        )
