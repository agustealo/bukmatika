from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Work,
)


class LocalImportIdentityConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExistingLocalImport:
    source_record_id: UUID
    work: Work
    edition: Edition
    asset: Asset
    library_entry: LibraryEntry
    stored_object: StoredObject


class LocalImportRepository:
    """Persistence boundary for principal-scoped user-supplied local Assets."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_source(self, source_record_id: UUID) -> SourceRecord:
        source = await self._session.scalar(
            select(SourceRecord)
            .where(SourceRecord.id == source_record_id)
            .with_for_update()
        )
        if source is None:
            raise LocalImportIdentityConflict("Local import source disappeared during ingestion")
        return source

    async def existing_for_source(
        self,
        *,
        principal_id: UUID,
        source_record_id: UUID,
    ) -> ExistingLocalImport | None:
        rows = (
            await self._session.execute(
                select(Work, Edition, Asset, LibraryEntry, StoredObject)
                .join(Edition, Edition.work_id == Work.id)
                .join(Asset, Asset.edition_id == Edition.id)
                .join(
                    SourceRecordLink,
                    and_(
                        SourceRecordLink.entity_type == "asset",
                        SourceRecordLink.entity_id == Asset.id,
                    ),
                )
                .join(
                    LibraryEntry,
                    and_(
                        LibraryEntry.principal_id == principal_id,
                        LibraryEntry.edition_id == Edition.id,
                    ),
                )
                .join(StoredObject, StoredObject.id == Asset.stored_object_id)
                .where(SourceRecordLink.source_record_id == source_record_id)
            )
        ).all()
        if not rows:
            return None
        if len(rows) != 1:
            raise LocalImportIdentityConflict(
                "Local import source resolves to multiple canonical Assets"
            )
        work, edition, asset, library_entry, stored_object = rows[0]
        return ExistingLocalImport(
            source_record_id=source_record_id,
            work=work,
            edition=edition,
            asset=asset,
            library_entry=library_entry,
            stored_object=stored_object,
        )

    async def create_asset(
        self,
        *,
        edition_id: UUID,
        format_name: str,
        media_type: str,
        stored_object_id: UUID,
        byte_size: int,
    ) -> Asset:
        asset = Asset(
            edition_id=edition_id,
            format=format_name,
            media_type=media_type,
            remote_url=None,
            stored_object_id=stored_object_id,
            byte_size=byte_size,
        )
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def document_for_asset(self, asset_id: UUID) -> Document | None:
        return await self._session.scalar(select(Document).where(Document.asset_id == asset_id))


__all__ = [
    "ExistingLocalImport",
    "LocalImportIdentityConflict",
    "LocalImportRepository",
]
