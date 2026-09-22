from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Principal, StoredObject, Work
from bukmatika.research import ResearchSearchRequest, ResearchSelectionDenied, ResearchService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_research_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    text: str,
    work_level: bool = False,
) -> tuple[LibraryEntry, Document, DocumentSection, DocumentChunk, Work, Edition]:
    work = Work(
        canonical_title=f"Research Work {suffix}",
        normalized_title=f"research work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/research/{suffix}",
        byte_size=max(1, len(text.encode("utf-8"))),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Research Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/{suffix}.txt",
        stored_object_id=stored.id,
        byte_size=stored.byte_size,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=None if work_level else edition.id,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()

    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading=f"Section {suffix}",
        locator={"page": 1, "section": suffix},
        text=text,
    )
    session.add(section)
    await session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        section_id=section.id,
        ordinal=0,
        char_start=0,
        char_end=len(text),
        text=text,
    )
    session.add(chunk)
    await session.flush()
    return entry, document, section, chunk, work, edition


async def test_research_searches_only_selected_owned_books_with_exact_citations(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="research-owner")
    session.add(principal)
    await session.flush()

    first, first_document, first_section, first_chunk, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="atlantic",
        text="Maritime trade crossed the Atlantic through several documented routes.",
    )
    second, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="mediterranean",
        text="Mediterranean maritime trade connected ports and merchant communities.",
    )
    unselected, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="astronomy",
        text="Maritime astronomy helped navigators estimate position at sea.",
    )

    response = await ResearchService(session_scope_factory=_scope(session)).search(
        principal_id=principal.id,
        request=ResearchSearchRequest(
            query="maritime trade",
            library_entry_ids=[first.id, second.id],
            limit=10,
        ),
    )

    assert response.selected_library_entry_ids == [first.id, second.id]
    assert len(response.passages) == 2
    selected_ids = {passage.library_entry_id for passage in response.passages}
    assert selected_ids == {first.id, second.id}
    assert unselected.id not in selected_ids

    first_passage = next(
        passage for passage in response.passages if passage.library_entry_id == first.id
    )
    assert first_passage.document_id == first_document.id
    assert first_passage.section_id == first_section.id
    assert first_passage.chunk_id == first_chunk.id
    assert first_passage.locator == {"page": 1, "section": "atlantic"}
    assert first_passage.char_start == 0
    assert first_passage.char_end == len(first_chunk.text)
    assert first_passage.score > 0


async def test_research_rejects_cross_principal_selection(session: AsyncSession) -> None:
    owner = Principal(kind="local", external_subject="research-owner-two")
    intruder = Principal(kind="local", external_subject="research-intruder")
    session.add_all([owner, intruder])
    await session.flush()
    entry, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=owner,
        suffix="private",
        text="Private maritime research notes are searchable only by their owner.",
    )

    with pytest.raises(ResearchSelectionDenied, match="unavailable"):
        await ResearchService(session_scope_factory=_scope(session)).search(
            principal_id=intruder.id,
            request=ResearchSearchRequest(
                query="maritime",
                library_entry_ids=[entry.id],
            ),
        )


async def test_overlapping_work_and_edition_selection_deduplicates_passages(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="research-overlap")
    session.add(principal)
    await session.flush()
    work_entry, document, _, _, work, edition = await _seed_research_book(
        session,
        principal=principal,
        suffix="overlap",
        text="Maritime exchange appears once even when ownership paths overlap.",
        work_level=True,
    )
    edition_entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add(edition_entry)
    await session.flush()

    response = await ResearchService(session_scope_factory=_scope(session)).search(
        principal_id=principal.id,
        request=ResearchSearchRequest(
            query="maritime exchange",
            library_entry_ids=[edition_entry.id, work_entry.id],
            limit=10,
        ),
    )

    assert len(response.passages) == 1
    assert response.passages[0].document_id == document.id
    assert response.passages[0].library_entry_id == edition_entry.id


async def test_selected_book_without_processed_document_returns_no_passage(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="research-unprocessed")
    work = Work(canonical_title="Unprocessed Work", normalized_title="unprocessed work")
    session.add_all([principal, work])
    await session.flush()
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=None,
        status="saved",
    )
    session.add(entry)
    await session.flush()

    response = await ResearchService(session_scope_factory=_scope(session)).search(
        principal_id=principal.id,
        request=ResearchSearchRequest(
            query="anything",
            library_entry_ids=[entry.id],
        ),
    )

    assert response.passages == []
