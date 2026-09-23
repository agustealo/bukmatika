from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.events import SemanticEventType
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.research import (
    ResearchCompareRequest,
    ResearchSearchRequest,
    ResearchSelectionDenied,
    ResearchService,
)


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


async def _seed_additional_edition(
    session: AsyncSession,
    *,
    work: Work,
    suffix: str,
    text: str,
) -> tuple[Edition, Document]:
    stored = StoredObject(
        sha256=(f"extra-{suffix}".encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/research/extra-{suffix}",
        byte_size=max(1, len(text.encode("utf-8"))),
        media_type="text/plain",
    )
    session.add(stored)
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Alternate Edition {suffix}",
        language="en",
        publication_year=1910,
    )
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/extra-{suffix}.txt",
        stored_object_id=stored.id,
        byte_size=stored.byte_size,
    )
    session.add(asset)
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
        heading=f"Alternate Section {suffix}",
        locator={"page": 2, "section": f"alternate-{suffix}"},
        text=text,
    )
    session.add(section)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=0,
            char_start=0,
            char_end=len(text),
            text=text,
        )
    )
    await session.flush()
    return edition, document


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


async def test_compare_balances_passages_per_selected_source_and_records_event(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="research-compare-balanced")
    session.add(principal)
    await session.flush()
    first, first_document, first_section, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="compare-first",
        text="Maritime trade used coastal routes and seasonal winds.",
    )
    second, second_document, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="compare-second",
        text="Maritime trade also depended on port records and merchant networks.",
    )
    extra_text = "Maritime trade records identify another route through the same first source."
    session.add(
        DocumentChunk(
            document_id=first_document.id,
            section_id=first_section.id,
            ordinal=1,
            char_start=100,
            char_end=100 + len(extra_text),
            text=extra_text,
        )
    )
    await session.flush()

    response = await ResearchService(session_scope_factory=_scope(session)).compare(
        principal_id=principal.id,
        request=ResearchCompareRequest(
            query="maritime trade",
            library_entry_ids=[first.id, second.id],
            per_source_limit=1,
        ),
    )

    assert [source.library_entry_id for source in response.sources] == [first.id, second.id]
    assert [len(source.passages) for source in response.sources] == [1, 1]
    assert response.sources[0].passages[0].library_entry_id == first.id
    assert response.sources[1].passages[0].library_entry_id == second.id
    assert response.sources[1].passages[0].document_id == second_document.id

    event = await session.scalar(
        select(InteractionEvent)
        .where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == SemanticEventType.RESEARCH_COMPARISON_COMPLETED.value,
        )
        .order_by(InteractionEvent.occurred_at.desc())
        .limit(1)
    )
    assert event is not None
    assert event.context["source_count"] == 2
    assert event.context["matched_source_count"] == 2
    assert event.context["passage_count"] == 2
    assert event.context["per_source_limit"] == 1


async def test_compare_retains_source_when_query_has_no_match(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="research-compare-no-match")
    session.add(principal)
    await session.flush()
    first, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="compare-match",
        text="Celestial navigation used stars to estimate position.",
    )
    second, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="compare-empty",
        text="Agricultural ledgers describe harvest yields and land use.",
    )

    response = await ResearchService(session_scope_factory=_scope(session)).compare(
        principal_id=principal.id,
        request=ResearchCompareRequest(
            query="celestial navigation",
            library_entry_ids=[first.id, second.id],
        ),
    )

    assert len(response.sources[0].passages) == 1
    assert response.sources[1].library_entry_id == second.id
    assert response.sources[1].passages == []


async def test_compare_exposes_work_level_editions_and_preserves_overlapping_source_identity(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="research-compare-editions")
    session.add(principal)
    await session.flush()
    work_entry, _, _, _, work, first_edition = await _seed_research_book(
        session,
        principal=principal,
        suffix="edition-one",
        text="Treaty language differs across surviving printed editions.",
        work_level=True,
    )
    second_edition, _ = await _seed_additional_edition(
        session,
        work=work,
        suffix="edition-two",
        text="Treaty language in the later edition includes a revised clause.",
    )
    edition_entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=first_edition.id,
        status="saved",
    )
    session.add(edition_entry)
    await session.flush()

    response = await ResearchService(session_scope_factory=_scope(session)).compare(
        principal_id=principal.id,
        request=ResearchCompareRequest(
            query="treaty language",
            library_entry_ids=[work_entry.id, edition_entry.id],
            per_source_limit=3,
        ),
    )

    work_source, edition_source = response.sources
    assert work_source.library_entry_id == work_entry.id
    assert {item.edition_id for item in work_source.available_editions} == {
        first_edition.id,
        second_edition.id,
    }
    assert {passage.edition_id for passage in work_source.passages} == {
        first_edition.id,
        second_edition.id,
    }
    assert edition_source.library_entry_id == edition_entry.id
    assert [item.edition_id for item in edition_source.available_editions] == [first_edition.id]
    assert {passage.edition_id for passage in edition_source.passages} == {first_edition.id}


async def test_compare_rejects_cross_principal_source(session: AsyncSession) -> None:
    owner = Principal(kind="local", external_subject="research-compare-owner")
    intruder = Principal(kind="local", external_subject="research-compare-intruder")
    session.add_all([owner, intruder])
    await session.flush()
    owned, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=intruder,
        suffix="compare-owned",
        text="Maritime evidence owned by the current principal.",
    )
    foreign, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=owner,
        suffix="compare-foreign",
        text="Maritime evidence owned by another principal.",
    )

    with pytest.raises(ResearchSelectionDenied, match="unavailable"):
        await ResearchService(session_scope_factory=_scope(session)).compare(
            principal_id=intruder.id,
            request=ResearchCompareRequest(
                query="maritime",
                library_entry_ids=[owned.id, foreign.id],
            ),
        )


def test_compare_request_requires_two_to_six_unique_sources() -> None:
    first = Principal().id
    second = Principal().id

    with pytest.raises(ValidationError):
        ResearchCompareRequest(query="trade", library_entry_ids=[first])
    with pytest.raises(ValidationError):
        ResearchCompareRequest(query="trade", library_entry_ids=[first, first])
    with pytest.raises(ValidationError):
        ResearchCompareRequest(
            query="trade",
            library_entry_ids=[first, second, *(Principal().id for _ in range(5))],
        )
