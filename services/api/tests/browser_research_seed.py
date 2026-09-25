from __future__ import annotations

import asyncio
import hashlib
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


@dataclass(frozen=True)
class BrowserSource:
    title: str
    author: str
    edition_title: str
    heading: str
    text: str
    publication_year: int
    storage_slug: str


SOURCES = (
    BrowserSource(
        title="Browser Research Evidence",
        author="Browser Journey Author",
        edition_title="Browser Journey Edition",
        heading="Navigation evidence",
        text=(
            "Mariners mapped obsidian navigation routes across the old world before 1492.\n\n"
            "A second passage records agricultural exchange and navigation evidence."
        ),
        publication_year=1491,
        storage_slug="research-match",
    ),
    BrowserSource(
        title="Browser No Match Evidence",
        author="Browser Comparison Author",
        edition_title="Browser Comparison Edition",
        heading="Kiln records",
        text=(
            "Ceramic kiln accounts record clay temperatures, glazing methods, "
            "and workshop tools.\n\n"
            "The surviving ledger lists fuel deliveries and seasonal firing schedules."
        ),
        publication_year=1490,
        storage_slug="research-no-match",
    ),
)


async def _seed_source(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    source: BrowserSource,
) -> UUID:
    existing = await database_session.scalar(
        select(LibraryEntry.id)
        .join(Work, Work.id == LibraryEntry.work_id)
        .where(
            LibraryEntry.principal_id == principal_id,
            Work.canonical_title == source.title,
        )
        .limit(1)
    )
    if existing is not None:
        return existing

    digest = hashlib.sha256(
        f"{principal_id}:{source.title}:{source.text}".encode()
    ).hexdigest()

    work = Work(
        canonical_title=source.title,
        normalized_title=source.title.casefold(),
    )
    contributor = Contributor(
        display_name=source.author,
        normalized_name=source.author.casefold(),
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
        title=source.edition_title,
        language="en",
        publication_year=source.publication_year,
        publisher="Bukmatika browser proof",
        edition_statement="Deterministic browser evidence fixture",
    )
    stored_object = StoredObject(
        sha256=digest,
        storage_key=f"browser-e2e/{principal_id}/{source.storage_slug}.txt",
        byte_size=len(source.text.encode()),
        media_type="text/plain",
    )
    database_session.add_all((edition, stored_object))
    await database_session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=(
            f"https://example.invalid/browser-e2e/{principal_id}/{source.storage_slug}.txt"
        ),
        stored_object_id=stored_object.id,
        byte_size=len(source.text.encode()),
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
        heading=source.heading,
        locator={"section": 1},
        text=source.text,
    )
    database_session.add(section)
    await database_session.flush()

    database_session.add(
        DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=0,
            char_start=0,
            char_end=len(source.text),
            text=source.text,
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


async def seed(principal_id: UUID) -> list[UUID]:
    async with session_scope() as database_session:
        return [
            await _seed_source(
                database_session,
                principal_id=principal_id,
                source=source,
            )
            for source in SOURCES
        ]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_research_seed.py <principal-id>")
    principal_id = UUID(sys.argv[1])
    library_entry_ids = asyncio.run(seed(principal_id))
    print(",".join(str(library_entry_id) for library_entry_id in library_entry_ids))


if __name__ == "__main__":
    main()
