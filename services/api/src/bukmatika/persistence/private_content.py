from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.models import (
    Asset,
    LibraryEntry,
    SourceRecord,
    SourceRecordLink,
)

_LOCAL_IMPORT_PROVIDER = "local-import"


class PrivateContentAccessRepository:
    """Principal access checks for content whose provenance is private by construction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def asset_accessible(
        self,
        *,
        principal_id: UUID | None,
        asset_id: UUID,
    ) -> bool:
        edition_id = await self._session.scalar(
            select(Asset.edition_id).where(Asset.id == asset_id)
        )
        if edition_id is None:
            return True
        if not await self._asset_is_private_local_import(asset_id):
            return True
        if principal_id is None:
            return False
        owner_entry_id = await self._session.scalar(
            select(LibraryEntry.id)
            .where(
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.edition_id == edition_id,
            )
            .limit(1)
        )
        return owner_entry_id is not None

    async def document_accessible(
        self,
        *,
        principal_id: UUID | None,
        document_id: UUID,
    ) -> bool:
        asset_id = await self._session.scalar(
            select(Document.asset_id).where(Document.id == document_id)
        )
        if asset_id is None:
            return True
        return await self.asset_accessible(
            principal_id=principal_id,
            asset_id=asset_id,
        )

    async def _asset_is_private_local_import(self, asset_id: UUID) -> bool:
        source_link_id = await self._session.scalar(
            select(SourceRecordLink.id)
            .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
            .where(
                SourceRecord.provider == _LOCAL_IMPORT_PROVIDER,
                SourceRecordLink.entity_type == "asset",
                SourceRecordLink.entity_id == asset_id,
            )
            .limit(1)
        )
        return source_link_id is not None


__all__ = ["PrivateContentAccessRepository"]
