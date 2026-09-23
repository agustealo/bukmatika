from collections import defaultdict
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.library_organization_models import (
    LibraryCollection,
    LibraryCollectionEntry,
    LibraryEntryTag,
    LibraryTag,
)
from bukmatika.persistence.models import LibraryEntry


class LibraryOrganizationNotFound(LookupError):
    pass


class LibraryOrganizationConflict(RuntimeError):
    pass


class LibraryOrganizationRepository:
    """Principal-owned organization authority around canonical LibraryEntry rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def require_entry(self, principal_id: UUID, library_entry_id: UUID) -> LibraryEntry:
        entry = await self._session.scalar(
            select(LibraryEntry).where(
                LibraryEntry.id == library_entry_id,
                LibraryEntry.principal_id == principal_id,
            )
        )
        if entry is None:
            raise LibraryOrganizationNotFound("Library entry is unavailable")
        return entry

    async def require_collection(
        self,
        principal_id: UUID,
        collection_id: UUID,
        *,
        lock: bool = False,
    ) -> LibraryCollection:
        statement = select(LibraryCollection).where(
            LibraryCollection.id == collection_id,
            LibraryCollection.principal_id == principal_id,
        )
        if lock:
            statement = statement.with_for_update()
        collection = await self._session.scalar(statement)
        if collection is None:
            raise LibraryOrganizationNotFound("Collection is unavailable")
        return collection

    async def require_tag(
        self,
        principal_id: UUID,
        tag_id: UUID,
        *,
        lock: bool = False,
    ) -> LibraryTag:
        statement = select(LibraryTag).where(
            LibraryTag.id == tag_id,
            LibraryTag.principal_id == principal_id,
        )
        if lock:
            statement = statement.with_for_update()
        tag = await self._session.scalar(statement)
        if tag is None:
            raise LibraryOrganizationNotFound("Tag is unavailable")
        return tag

    async def create_collection(
        self,
        *,
        principal_id: UUID,
        name: str,
        normalized_name: str,
        description: str | None,
    ) -> LibraryCollection:
        statement = (
            insert(LibraryCollection)
            .values(
                principal_id=principal_id,
                name=name,
                normalized_name=normalized_name,
                description=description,
            )
            .on_conflict_do_nothing(constraint="uq_library_collection_principal_name")
            .returning(LibraryCollection)
        )
        created = (await self._session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created
        existing = await self._session.scalar(
            select(LibraryCollection).where(
                LibraryCollection.principal_id == principal_id,
                LibraryCollection.normalized_name == normalized_name,
            )
        )
        if existing is None:
            raise RuntimeError("Collection upsert returned no row")
        return existing

    async def update_collection(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
        name: str,
        normalized_name: str,
        description: str | None,
    ) -> LibraryCollection:
        collection = await self.require_collection(principal_id, collection_id, lock=True)
        duplicate = await self._session.scalar(
            select(LibraryCollection.id).where(
                LibraryCollection.principal_id == principal_id,
                LibraryCollection.normalized_name == normalized_name,
                LibraryCollection.id != collection_id,
            )
        )
        if duplicate is not None:
            raise LibraryOrganizationConflict("A collection with this name already exists")
        collection.name = name
        collection.normalized_name = normalized_name
        collection.description = description
        await self._session.flush()
        return collection

    async def delete_collection(self, *, principal_id: UUID, collection_id: UUID) -> None:
        await self.require_collection(principal_id, collection_id)
        await self._session.execute(
            delete(LibraryCollection).where(
                LibraryCollection.id == collection_id,
                LibraryCollection.principal_id == principal_id,
            )
        )

    async def add_collection_entry(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
        library_entry_id: UUID,
    ) -> None:
        await self.require_collection(principal_id, collection_id)
        await self.require_entry(principal_id, library_entry_id)
        await self._session.execute(
            insert(LibraryCollectionEntry)
            .values(collection_id=collection_id, library_entry_id=library_entry_id)
            .on_conflict_do_nothing(
                index_elements=[
                    LibraryCollectionEntry.collection_id,
                    LibraryCollectionEntry.library_entry_id,
                ]
            )
        )

    async def remove_collection_entry(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
        library_entry_id: UUID,
    ) -> None:
        await self.require_collection(principal_id, collection_id)
        await self.require_entry(principal_id, library_entry_id)
        await self._session.execute(
            delete(LibraryCollectionEntry).where(
                LibraryCollectionEntry.collection_id == collection_id,
                LibraryCollectionEntry.library_entry_id == library_entry_id,
            )
        )

    async def assign_tag(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        name: str,
        normalized_name: str,
    ) -> LibraryTag:
        await self.require_entry(principal_id, library_entry_id)
        statement = (
            insert(LibraryTag)
            .values(
                principal_id=principal_id,
                name=name,
                normalized_name=normalized_name,
            )
            .on_conflict_do_nothing(constraint="uq_library_tag_principal_name")
            .returning(LibraryTag)
        )
        tag = (await self._session.execute(statement)).scalar_one_or_none()
        if tag is None:
            tag = await self._session.scalar(
                select(LibraryTag).where(
                    LibraryTag.principal_id == principal_id,
                    LibraryTag.normalized_name == normalized_name,
                )
            )
        if tag is None:
            raise RuntimeError("Tag upsert returned no row")
        await self._session.execute(
            insert(LibraryEntryTag)
            .values(library_entry_id=library_entry_id, tag_id=tag.id)
            .on_conflict_do_nothing(
                index_elements=[LibraryEntryTag.library_entry_id, LibraryEntryTag.tag_id]
            )
        )
        return tag

    async def update_tag(
        self,
        *,
        principal_id: UUID,
        tag_id: UUID,
        name: str,
        normalized_name: str,
    ) -> LibraryTag:
        tag = await self.require_tag(principal_id, tag_id, lock=True)
        duplicate = await self._session.scalar(
            select(LibraryTag.id).where(
                LibraryTag.principal_id == principal_id,
                LibraryTag.normalized_name == normalized_name,
                LibraryTag.id != tag_id,
            )
        )
        if duplicate is not None:
            raise LibraryOrganizationConflict("A tag with this name already exists")
        tag.name = name
        tag.normalized_name = normalized_name
        await self._session.flush()
        return tag

    async def remove_entry_tag(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        tag_id: UUID,
    ) -> None:
        await self.require_entry(principal_id, library_entry_id)
        await self.require_tag(principal_id, tag_id)
        await self._session.execute(
            delete(LibraryEntryTag).where(
                LibraryEntryTag.library_entry_id == library_entry_id,
                LibraryEntryTag.tag_id == tag_id,
            )
        )

    async def delete_tag(self, *, principal_id: UUID, tag_id: UUID) -> None:
        await self.require_tag(principal_id, tag_id)
        await self._session.execute(
            delete(LibraryTag).where(
                LibraryTag.id == tag_id,
                LibraryTag.principal_id == principal_id,
            )
        )

    async def collections(self, principal_id: UUID) -> list[tuple[LibraryCollection, int]]:
        rows = (
            await self._session.execute(
                select(LibraryCollection, func.count(LibraryCollectionEntry.library_entry_id))
                .outerjoin(
                    LibraryCollectionEntry,
                    LibraryCollectionEntry.collection_id == LibraryCollection.id,
                )
                .where(LibraryCollection.principal_id == principal_id)
                .group_by(LibraryCollection.id)
                .order_by(LibraryCollection.normalized_name, LibraryCollection.id)
            )
        ).all()
        return [(collection, int(count)) for collection, count in rows]

    async def tags(self, principal_id: UUID) -> list[tuple[LibraryTag, int]]:
        rows = (
            await self._session.execute(
                select(LibraryTag, func.count(LibraryEntryTag.library_entry_id))
                .outerjoin(LibraryEntryTag, LibraryEntryTag.tag_id == LibraryTag.id)
                .where(LibraryTag.principal_id == principal_id)
                .group_by(LibraryTag.id)
                .order_by(LibraryTag.normalized_name, LibraryTag.id)
            )
        ).all()
        return [(tag, int(count)) for tag, count in rows]

    async def organization_for_entries(
        self,
        *,
        principal_id: UUID,
        entry_ids: list[UUID],
    ) -> tuple[
        dict[UUID, list[LibraryCollection]],
        dict[UUID, list[LibraryTag]],
    ]:
        collections_by_entry: dict[UUID, list[LibraryCollection]] = defaultdict(list)
        tags_by_entry: dict[UUID, list[LibraryTag]] = defaultdict(list)
        if not entry_ids:
            return {}, {}

        collection_rows = (
            await self._session.execute(
                select(LibraryCollectionEntry.library_entry_id, LibraryCollection)
                .join(
                    LibraryCollection,
                    LibraryCollection.id == LibraryCollectionEntry.collection_id,
                )
                .where(
                    LibraryCollection.principal_id == principal_id,
                    LibraryCollectionEntry.library_entry_id.in_(entry_ids),
                )
                .order_by(LibraryCollection.normalized_name, LibraryCollection.id)
            )
        ).all()
        for entry_id, collection in collection_rows:
            collections_by_entry[entry_id].append(collection)

        tag_rows = (
            await self._session.execute(
                select(LibraryEntryTag.library_entry_id, LibraryTag)
                .join(LibraryTag, LibraryTag.id == LibraryEntryTag.tag_id)
                .where(
                    LibraryTag.principal_id == principal_id,
                    LibraryEntryTag.library_entry_id.in_(entry_ids),
                )
                .order_by(LibraryTag.normalized_name, LibraryTag.id)
            )
        ).all()
        for entry_id, tag in tag_rows:
            tags_by_entry[entry_id].append(tag)

        return dict(collections_by_entry), dict(tags_by_entry)

    async def collection_entry_ids(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
    ) -> set[UUID]:
        await self.require_collection(principal_id, collection_id)
        values = await self._session.scalars(
            select(LibraryCollectionEntry.library_entry_id).where(
                LibraryCollectionEntry.collection_id == collection_id
            )
        )
        return set(values)

    async def tag_entry_ids(self, *, principal_id: UUID, tag_id: UUID) -> set[UUID]:
        await self.require_tag(principal_id, tag_id)
        values = await self._session.scalars(
            select(LibraryEntryTag.library_entry_id).where(LibraryEntryTag.tag_id == tag_id)
        )
        return set(values)
