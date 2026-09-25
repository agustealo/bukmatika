from __future__ import annotations

import asyncio
import hashlib
import sys
from uuid import UUID

from sqlalchemy import select

from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    LibraryEntry,
    StoredObject,
    Work,
    WorkContributor,
)

TITLE = "Browser Research Evidence"
AUTHOR = "Browser Journey Author"
TEXT = (
    "Mariners mapped obsidian navigation routes across the old world before 1492.\n\n"
    "A second passage records agricultural exchange and navigation evidence."
)


async def seed(principal_id: UUID) -> UUID:
    async with session_scope() as database_session:
        existing = await database_session.scalar(
            select(LibraryEntry.id)
            .join(Work, Work.id == LibraryEntry.work_id)
            .where(
                LibraryEntry.principal_id == principal_id,
                Work.canonical_title == TITLE,
            )
            .limit(1)
        )
        if existing is not None:
            return existing

        digest = hashlib.sha256(f"{principal_id}:{TEXT}".encode()).hexdigest()

        work = Work(canonical_title=TITLE, normalized_title=TITLE.casefold())
        contributor = Contributor(
            display_name=AUTHOR,
            normalized_name=AUTHOR.casefold(),
        )
        database_session.add_all((work, contributor))
        await database_session.flush()

        database_session.add(
            WorkContributor(
                work_id=work.id,
                contributor_id=contributor.id,
                role="author",
            )
        )

        edition = Edition(
            work_id=work.id,
            title="Browser Journey Edition",
            language="en",
            publication_year=1491,
            publisher="Bukmatika browser proof",
            edition_statement="Deterministic browser evidence fixture",
        )
        stored_object = StoredObject(
            sha256=digest,
            storage_key=f"browser-e2e/{principal_id}/research.txt",
            byte_size=len(TEXT.encode()),
            media_type="text/plain",
        )
        database_session.add_all((edition, stored_object))
        await database_session.flush()

        asset = Asset(
            edition_id=edition.id,
            format="TXT",
            media_type="text/plain",
            remote_url=f"https://example.invalid/browser-e2e/{principal_id}/research.txt",
            stored_object_id=stored_object.id,
            byte_size=len(TEXT.encode()),
        )
        database_session.add(asset)
        await database_session.flush()

        document = Document(
            asset_id=asset.id,
            stored_object_id=stored_object.id,
            source_sha256=digest,
            format="TXT",
            parser_name="browser-e2e",
            parser_version="1",
            section_count=1,
            chunk_count=1,
        )
        database_session.add(document)
        await database_session.flush()

        section = DocumentSection(
            document_id=document.id,
            ordinal=0,
            heading="Navigation evidence",
            locator={"section": 1},
            text=TEXT,
        )
        database_session.add(section)
        await database_session.flush()

        database_session.add(
            DocumentChunk(
                document_id=document.id,
                section_id=section.id,
                ordinal=0,
                char_start=0,
                char_end=len(TEXT),
                text=TEXT,
            )
        )

        library_entry = LibraryEntry(
            principal_id=principal_id,
            work_id=work.id,
            edition_id=edition.id,
            status="saved",
        )
        database_session.add(library_entry)
        await database_session.flush()
        return library_entry.id


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_research_seed.py <principal-id>")
    principal_id = UUID(sys.argv[1])
    library_entry_id = asyncio.run(seed(principal_id))
    print(library_entry_id)


if __name__ == "__main__":
    main()
