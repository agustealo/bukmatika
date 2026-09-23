from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.readers import ReaderAccessDenied
from bukmatika.reader import ReaderService
from bukmatika.reader.domain import ReaderNavigationKind


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_navigation_document(
    session: AsyncSession,
    *,
    suffix: str,
    format_name: str,
    section_specs: list[tuple[str | None, dict[str, object], str]],
) -> tuple[Principal, LibraryEntry, Document, list[DocumentSection]]:
    principal = Principal(kind="local", external_subject=f"reader-navigation-{suffix}")
    work = Work(
        canonical_title=f"Navigation Work {suffix}",
        normalized_title=f"navigation work {suffix}",
    )
    session.add_all([principal, work])
    await session.flush()

    edition = Edition(work_id=work.id, title=f"Navigation Edition {suffix}", language="en")
    media_type = "application/pdf" if format_name == "PDF" else "application/epub+zip"
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/navigation/{suffix}",
        byte_size=512,
        media_type=media_type,
    )
    session.add_all([edition, stored])
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format=format_name,
        media_type=media_type,
        remote_url=f"https://example.org/{suffix}.{format_name.casefold()}",
        stored_object_id=stored.id,
        byte_size=512,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format=format_name,
        parser_name="navigation-test",
        parser_version="1",
        section_count=len(section_specs),
        chunk_count=len(section_specs),
    )
    session.add(document)
    await session.flush()

    sections = [
        DocumentSection(
            document_id=document.id,
            ordinal=ordinal,
            heading=heading,
            locator=locator,
            text=text,
        )
        for ordinal, (heading, locator, text) in enumerate(section_specs)
    ]
    session.add_all(sections)
    await session.flush()
    return principal, entry, document, sections


async def test_pdf_navigation_uses_only_canonical_text_bearing_pages(
    session: AsyncSession,
) -> None:
    principal, entry, document, sections = await _seed_navigation_document(
        session,
        suffix="pdf",
        format_name="PDF",
        section_specs=[
            (None, {"page": 1}, "Page one text."),
            (None, {"page": 3}, "Page three text."),
            (None, {"page": 5}, "Page five text."),
        ],
    )

    response = await ReaderService(session_scope_factory=_scope(session)).navigation(
        principal_id=principal.id,
        library_entry_id=entry.id,
        document_id=document.id,
    )

    assert response.kind == ReaderNavigationKind.PDF_PAGES
    assert [item.label for item in response.items] == ["Page 1", "Page 3", "Page 5"]
    assert [item.section_id for item in response.items] == [section.id for section in sections]
    assert [item.section_ordinal for item in response.items] == [0, 1, 2]


async def test_epub_navigation_groups_spine_and_uses_first_heading_as_label(
    session: AsyncSession,
) -> None:
    principal, entry, document, sections = await _seed_navigation_document(
        session,
        suffix="epub",
        format_name="EPUB",
        section_specs=[
            (
                None,
                {"spine": 1, "item": "OEBPS/preface.xhtml", "section": 1, "element": "p"},
                "Introductory copy.",
            ),
            (
                "Preface",
                {"spine": 1, "item": "OEBPS/preface.xhtml", "section": 2, "element": "h1"},
                "Preface",
            ),
            (
                "Chapter One",
                {"spine": 2, "item": "OEBPS/chapter1.xhtml", "section": 1, "element": "h1"},
                "Chapter One",
            ),
            (
                None,
                {"spine": 2, "item": "OEBPS/chapter1.xhtml", "section": 2, "element": "p"},
                "Chapter body.",
            ),
        ],
    )

    response = await ReaderService(session_scope_factory=_scope(session)).navigation(
        principal_id=principal.id,
        library_entry_id=entry.id,
        document_id=document.id,
    )

    assert response.kind == ReaderNavigationKind.EPUB_SPINE
    assert [item.label for item in response.items] == ["Preface", "Chapter One"]
    assert [item.section_id for item in response.items] == [sections[0].id, sections[2].id]
    assert [item.section_ordinal for item in response.items] == [0, 2]
    assert [item.key for item in response.items] == ["spine:1", "spine:2"]


async def test_navigation_rejects_another_principal_with_exact_ids(
    session: AsyncSession,
) -> None:
    principal, entry, document, _ = await _seed_navigation_document(
        session,
        suffix="ownership",
        format_name="PDF",
        section_specs=[(None, {"page": 1}, "Owned page.")],
    )
    intruder = Principal(kind="local", external_subject="reader-navigation-intruder")
    session.add(intruder)
    await session.flush()

    assert intruder.id != principal.id
    with pytest.raises(ReaderAccessDenied, match="does not own"):
        await ReaderService(session_scope_factory=_scope(session)).navigation(
            principal_id=intruder.id,
            library_entry_id=entry.id,
            document_id=document.id,
        )


def test_reader_navigation_route_is_mounted() -> None:
    path = "/v1/library/{library_entry_id}/documents/{document_id}/navigation"
    mounted_routes = [
        (getattr(route, "name", None), getattr(route, "path", None))
        for route in app.routes
        if "/documents/" in str(getattr(route, "path", ""))
    ]
    mounted = [route for route in app.routes if getattr(route, "name", None) == "reader_navigation"]
    assert len(mounted) == 1, mounted_routes
    assert getattr(mounted[0], "path", None) == path
    assert "GET" in (getattr(mounted[0], "methods", set()) or set())
