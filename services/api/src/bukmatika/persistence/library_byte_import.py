from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Edition,
    Identifier,
    LibraryEntry,
    RightsDecision,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
)


@dataclass(frozen=True, slots=True)
class OwnedPortableAsset:
    library_entry: LibraryEntry
    asset: Asset


class LibraryPortableByteImportRepository:
    """Canonical identity, ownership, and rights reads for portable byte ingestion."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def asset_ids_for_identifier(self, scheme: str, normalized_value: str) -> set[UUID]:
        values = await self._session.scalars(
            select(Identifier.entity_id).where(
                Identifier.entity_type == "asset",
                Identifier.scheme == scheme,
                Identifier.normalized_value == normalized_value,
            )
        )
        return set(values.all())

    async def asset_ids_for_sources(self, sources: list[tuple[str, str]]) -> set[UUID]:
        if not sources:
            return set()
        result: set[UUID] = set()
        for provider, provider_record_id in sources:
            values = await self._session.scalars(
                select(SourceRecordLink.entity_id)
                .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
                .where(
                    SourceRecord.provider == provider,
                    SourceRecord.provider_record_id == provider_record_id,
                    SourceRecordLink.entity_type == "asset",
                )
            )
            result.update(values.all())
        return result

    async def asset_ids_for_content_sha(
        self,
        *,
        sha256: str,
        edition_id: UUID,
    ) -> set[UUID]:
        values = await self._session.scalars(
            select(Asset.id)
            .join(StoredObject, StoredObject.id == Asset.stored_object_id)
            .where(
                Asset.edition_id == edition_id,
                StoredObject.sha256 == sha256,
            )
        )
        return set(values.all())

    async def assets_for_edition(self, edition_id: UUID) -> list[Asset]:
        values = await self._session.scalars(
            select(Asset).where(Asset.edition_id == edition_id).order_by(Asset.id)
        )
        return list(values)

    async def asset(self, asset_id: UUID) -> Asset | None:
        return await self._session.get(Asset, asset_id)

    async def lock_owned_asset(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        work_id: UUID,
        edition_id: UUID,
        asset_id: UUID,
    ) -> OwnedPortableAsset | None:
        library_entry = await self._session.scalar(
            select(LibraryEntry)
            .where(
                LibraryEntry.id == library_entry_id,
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.work_id == work_id,
            )
            .with_for_update()
        )
        if library_entry is None:
            return None
        if library_entry.edition_id is not None and library_entry.edition_id != edition_id:
            return None

        asset = await self._session.scalar(
            select(Asset)
            .join(Edition, Edition.id == Asset.edition_id)
            .where(
                Asset.id == asset_id,
                Asset.edition_id == edition_id,
                Edition.work_id == work_id,
            )
            .with_for_update()
        )
        if asset is None:
            return None
        return OwnedPortableAsset(library_entry=library_entry, asset=asset)

    async def acquisition_for_asset(self, asset_id: UUID) -> Acquisition | None:
        return await self._session.scalar(
            select(Acquisition).where(Acquisition.asset_id == asset_id).with_for_update()
        )

    async def stored_object(self, stored_object_id: UUID) -> StoredObject | None:
        return await self._session.get(StoredObject, stored_object_id)

    async def latest_rights_decision(self, asset_id: UUID) -> RightsDecision | None:
        return await self._session.scalar(
            select(RightsDecision)
            .where(
                RightsDecision.subject_type == "asset",
                RightsDecision.subject_id == asset_id,
            )
            .order_by(RightsDecision.evaluated_at.desc(), RightsDecision.id.desc())
            .limit(1)
        )
