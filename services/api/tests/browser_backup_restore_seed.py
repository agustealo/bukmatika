import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy import select

from bukmatika.normalization import normalize_identifier, normalize_text
from bukmatika.persistence import session_scope
from bukmatika.persistence.library_organization_models import (
    LibraryCollection,
    LibraryCollectionEntry,
    LibraryEntryTag,
    LibrarySmartShelf,
    LibraryTag,
)
from bukmatika.persistence.models import Edition, Identifier, LibraryEntry, Principal, Work


TITLE = "The Browser Backup Ledger"
EDITION_TITLE = "The Browser Backup Ledger, Portable Edition"
COLLECTION_NAME = "Backup proof collection"
TAG_NAME = "backup-proof"
SMART_SHELF_NAME = "Backup proof shelf"


async def seed(principal_id: UUID) -> dict[str, str]:
    async with session_scope() as session:
        principal = await session.get(Principal, principal_id)
        if principal is None:
            raise RuntimeError("browser backup principal does not exist")

        identifier_value = f"browser-backup-{principal_id}"
        existing_id = await session.scalar(
            select(Identifier.entity_id).where(
                Identifier.entity_type == "work",
                Identifier.scheme == "browser-backup",
                Identifier.normalized_value == normalize_identifier(identifier_value),
            )
        )
        if existing_id is not None:
            work = await session.get(Work, existing_id)
            if work is None:
                raise RuntimeError("browser backup work identity is corrupt")
            entry = await session.scalar(
                select(LibraryEntry).where(
                    LibraryEntry.principal_id == principal_id,
                    LibraryEntry.work_id == work.id,
                )
            )
            if entry is None:
                raise RuntimeError("browser backup library entry is missing")
            return {
                "title": work.canonical_title,
                "collection": COLLECTION_NAME,
                "tag": TAG_NAME,
                "smart_shelf": SMART_SHELF_NAME,
                "library_entry_id": str(entry.id),
            }

        work = Work(canonical_title=TITLE, normalized_title=normalize_text(TITLE))
        session.add(work)
        await session.flush()

        edition = Edition(
            work_id=work.id,
            title=EDITION_TITLE,
            language="en",
            publication_year=1908,
            publisher="Bukmatika Browser Press",
            edition_statement="Portable proof edition",
        )
        session.add(edition)
        await session.flush()

        session.add_all(
            [
                Identifier(
                    entity_type="work",
                    entity_id=work.id,
                    scheme="browser-backup",
                    value=identifier_value,
                    normalized_value=normalize_identifier(identifier_value),
                ),
                Identifier(
                    entity_type="edition",
                    entity_id=edition.id,
                    scheme="browser-backup-edition",
                    value=identifier_value,
                    normalized_value=normalize_identifier(identifier_value),
                ),
            ]
        )

        entry = LibraryEntry(
            principal_id=principal_id,
            work_id=work.id,
            edition_id=edition.id,
            status="saved",
        )
        session.add(entry)
        await session.flush()

        collection = LibraryCollection(
            principal_id=principal_id,
            name=COLLECTION_NAME,
            normalized_name=normalize_text(COLLECTION_NAME),
            description="Portable browser backup acceptance proof",
        )
        tag = LibraryTag(
            principal_id=principal_id,
            name=TAG_NAME,
            normalized_name=normalize_text(TAG_NAME),
        )
        session.add_all([collection, tag])
        await session.flush()

        session.add_all(
            [
                LibraryCollectionEntry(collection_id=collection.id, library_entry_id=entry.id),
                LibraryEntryTag(library_entry_id=entry.id, tag_id=tag.id),
                LibrarySmartShelf(
                    principal_id=principal_id,
                    name=SMART_SHELF_NAME,
                    normalized_name=normalize_text(SMART_SHELF_NAME),
                    description="Restored from a Bukmatika backup",
                    reading_status=None,
                    collection_id=collection.id,
                    tag_id=None,
                ),
            ]
        )

        return {
            "title": TITLE,
            "collection": COLLECTION_NAME,
            "tag": TAG_NAME,
            "smart_shelf": SMART_SHELF_NAME,
            "library_entry_id": str(entry.id),
        }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_backup_restore_seed.py <principal-id>")
    result = asyncio.run(seed(UUID(sys.argv[1])))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
