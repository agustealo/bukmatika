from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, StoredObject, Work


@dataclass(frozen=True, slots=True)
class ReaderFormatSeed:
    title: str
    format: str
    media_type: str
    storage_suffix: str
    headings: tuple[str, str, str]
    passages: tuple[str, str, str]


def _long_passage(opening: str, label: str) -> str:
    supporting = [
        f"{label} supporting paragraph {index} keeps the canonical section taller than the viewport."
        for index in range(1, 25)
    ]
    return "\n".join((opening, *supporting))


FORMATS = (
    ReaderFormatSeed(
        title="Browser PDF Reader",
        format="PDF",
        media_type="application/pdf",
        storage_suffix="reader-proof.pdf",
        headings=(
            "PDF opening page",
            "PDF evidence page",
            "PDF closing page",
        ),
        passages=(
            _long_passage(
                "PDF page one introduces the canonical reader acceptance journey.",
                "PDF page one",
            ),
            _long_passage(
                "PDF page two preserves exact page navigation and source coordinates.",
                "PDF page two",
            ),
            _long_passage(
                "PDF page three proves durable progress, bookmarks, highlights, and notes.",
                "PDF page three",
            ),
        ),
    ),
    ReaderFormatSeed(
        title="Browser EPUB Reader",
        format="EPUB",
        media_type="application/epub+zip",
        storage_suffix="reader-proof.epub",
        headings=(
            "EPUB Opening",
            "EPUB Evidence",
            "EPUB Closing",
        ),
        passages=(
            _long_passage(
                "EPUB spine one introduces the canonical reader acceptance journey.",
                "EPUB spine one",
            ),
            _long_passage(
                "EPUB spine two preserves exact section navigation and source coordinates.",
                "EPUB spine two",
            ),
            _long_passage(
                "EPUB spine three proves durable reading progress and resume behavior.",
                "EPUB spine three",
            ),
        ),
    ),
)


async def _seed_format(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    fixture: ReaderFormatSeed,
) -> dict[str, object]:
    digest = hashlib.sha256(
        f"browser-reader-format:{principal_id}:{fixture.format}".encode()
    ).hexdigest()
    title = f"{fixture.title} {principal_id.hex[:8]}"

    work = Work(canonical_title=title, normalized_title=title.casefold())
    database_session.add(work)
    await database_session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"{title} Edition",
        language="en",
        publication_year=1902,
        publisher="Bukmatika browser proof",
        edition_statement=f"Deterministic {fixture.format} Reader acceptance fixture",
    )
    stored_object = StoredObject(
        sha256=digest,
        storage_key=f"browser-reader-format/{principal_id}/{fixture.storage_suffix}",
        byte_size=sum(len(passage.encode()) for passage in fixture.passages),
        media_type=fixture.media_type,
    )
    database_session.add_all((edition, stored_object))
    await database_session.flush()

    asset = Asset(
        edition_id=edition.id,
        format=fixture.format,
        media_type=fixture.media_type,
        remote_url=(
            f"https://example.invalid/browser-reader-format/{principal_id}/"
            f"{fixture.storage_suffix}"
        ),
        stored_object_id=stored_object.id,
        byte_size=stored_object.byte_size,
    )
    database_session.add(asset)
    await database_session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored_object.id,
        source_sha256=digest,
        format=fixture.format,
        parser_name="browser-reader-format",
        parser_version="1",
        section_count=len(fixture.passages),
        chunk_count=len(fixture.passages),
    )
    database_session.add(document)
    await database_session.flush()

    section_ids: list[str] = []
    for ordinal, (heading, passage) in enumerate(
        zip(fixture.headings, fixture.passages, strict=True)
    ):
        coordinate = ordinal + 1
        locator = {"page": coordinate} if fixture.format == "PDF" else {"spine": coordinate}
        section = DocumentSection(
            document_id=document.id,
            ordinal=ordinal,
            heading=heading,
            locator=locator,
            text=passage,
        )
        database_session.add(section)
        await database_session.flush()
        section_ids.append(str(section.id))
        database_session.add(
            DocumentChunk(
                document_id=document.id,
                section_id=section.id,
                ordinal=ordinal,
                char_start=0,
                char_end=len(passage),
                text=passage,
            )
        )

    entry = LibraryEntry(
        principal_id=principal_id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    database_session.add(entry)
    await database_session.flush()

    return {
        "title": title,
        "library_entry_id": str(entry.id),
        "document_id": str(document.id),
        "section_ids": section_ids,
        "headings": list(fixture.headings),
        "passages": list(fixture.passages),
    }


async def seed(principal_id: UUID) -> dict[str, object]:
    async with session_scope() as database_session:
        seeded = [
            await _seed_format(
                database_session,
                principal_id=principal_id,
                fixture=fixture,
            )
            for fixture in FORMATS
        ]
    return {"pdf": seeded[0], "epub": seeded[1]}


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_reader_formats_seed.py <principal-id>")
    principal_id = UUID(sys.argv[1])
    print(json.dumps(asyncio.run(seed(principal_id)), sort_keys=True))


if __name__ == "__main__":
    main()
