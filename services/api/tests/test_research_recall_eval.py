from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.research import (
    ResearchRecallCase,
    ResearchRecallEvaluator,
    ResearchRecallSuite,
    ResearchRecallSuiteInvalid,
    ResearchRecallTarget,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    text: str,
) -> tuple[LibraryEntry, Document, DocumentSection, DocumentChunk]:
    work = Work(
        canonical_title=f"Recall Work {suffix}",
        normalized_title=f"recall work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/recall/{suffix}",
        byte_size=len(text.encode("utf-8")),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Recall Edition {suffix}",
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
        edition_id=edition.id,
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
        heading=f"Recall {suffix}",
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
    return entry, document, section, chunk


def _target(
    document: Document,
    section: DocumentSection,
    chunk: DocumentChunk,
) -> ResearchRecallTarget:
    return ResearchRecallTarget(
        document_id=document.id,
        section_id=section.id,
        chunk_id=chunk.id,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
    )


async def test_recall_evaluation_measures_canonical_hit_and_rank(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="recall-hit-owner")
    session.add(principal)
    await session.flush()
    entry, document, section, chunk = await _seed_book(
        session,
        principal=principal,
        suffix="maritime",
        text="Maritime trade crossed the Atlantic through documented merchant routes.",
    )
    suite = ResearchRecallSuite(
        principal_id=principal.id,
        minimum_case_recall=1.0,
        minimum_macro_recall=1.0,
        cases=[
            ResearchRecallCase(
                case_id="maritime-exact",
                query="maritime trade",
                library_entry_ids=[entry.id],
                expected=[_target(document, section, chunk)],
                limit=5,
            )
        ],
    )

    result = await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(suite)

    assert result.passed is True
    assert result.micro_recall == 1.0
    assert result.macro_recall == 1.0
    assert result.mean_reciprocal_rank == 1.0
    assert result.cases[0].first_relevant_rank == 1
    assert result.cases[0].missed_chunk_ids == []
    assert result.cases[0].retrieved[0].chunk_id == chunk.id


async def test_recall_evaluation_surfaces_lexical_synonym_gap_without_faking_hit(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="recall-gap-owner")
    session.add(principal)
    await session.flush()
    entry, document, section, chunk = await _seed_book(
        session,
        principal=principal,
        suffix="synonym-gap",
        text="Maritime trade crossed the Atlantic through documented merchant routes.",
    )
    suite = ResearchRecallSuite(
        principal_id=principal.id,
        minimum_case_recall=1.0,
        minimum_macro_recall=1.0,
        cases=[
            ResearchRecallCase(
                case_id="ocean-commerce-synonym",
                query="ocean commerce",
                library_entry_ids=[entry.id],
                expected=[_target(document, section, chunk)],
                limit=5,
            )
        ],
    )

    result = await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(suite)

    assert result.passed is False
    assert result.micro_recall == 0.0
    assert result.macro_recall == 0.0
    assert result.mean_reciprocal_rank == 0.0
    assert result.cases[0].first_relevant_rank is None
    assert result.cases[0].missed_chunk_ids == [chunk.id]
    assert result.cases[0].retrieved == []


async def test_recall_evaluation_rejects_expected_chunk_outside_selected_owned_books(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="recall-scope-owner")
    session.add(principal)
    await session.flush()
    selected, _, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="selected",
        text="Atlantic navigation relied on winds and currents.",
    )
    _, foreign_document, foreign_section, foreign_chunk = await _seed_book(
        session,
        principal=principal,
        suffix="outside-selection",
        text="Merchant finance supported long-distance exchange.",
    )
    suite = ResearchRecallSuite(
        principal_id=principal.id,
        minimum_case_recall=1.0,
        minimum_macro_recall=1.0,
        cases=[
            ResearchRecallCase(
                case_id="out-of-scope-target",
                query="merchant finance",
                library_entry_ids=[selected.id],
                expected=[_target(foreign_document, foreign_section, foreign_chunk)],
            )
        ],
    )

    with pytest.raises(ResearchRecallSuiteInvalid, match="outside the selected owned books"):
        await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(suite)


async def test_recall_evaluation_rejects_cross_principal_library_selection(
    session: AsyncSession,
) -> None:
    owner = Principal(kind="local", external_subject="recall-principal-owner")
    intruder = Principal(kind="local", external_subject="recall-principal-intruder")
    session.add_all([owner, intruder])
    await session.flush()
    entry, document, section, chunk = await _seed_book(
        session,
        principal=owner,
        suffix="foreign-owner",
        text="Private shipping ledgers describe Atlantic merchant routes.",
    )
    suite = ResearchRecallSuite(
        principal_id=intruder.id,
        minimum_case_recall=1.0,
        minimum_macro_recall=1.0,
        cases=[
            ResearchRecallCase(
                case_id="cross-principal-selection",
                query="shipping ledgers",
                library_entry_ids=[entry.id],
                expected=[_target(document, section, chunk)],
            )
        ],
    )

    with pytest.raises(ResearchRecallSuiteInvalid, match="unavailable to this principal"):
        await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(suite)


async def test_recall_evaluation_rejects_stale_expected_coordinates(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="recall-stale-owner")
    session.add(principal)
    await session.flush()
    entry, document, section, chunk = await _seed_book(
        session,
        principal=principal,
        suffix="stale",
        text="Port records document the movement of ships and cargo.",
    )
    stale = ResearchRecallTarget(
        document_id=document.id,
        section_id=section.id,
        chunk_id=chunk.id,
        char_start=chunk.char_start,
        char_end=chunk.char_end - 1,
    )
    suite = ResearchRecallSuite(
        principal_id=principal.id,
        minimum_case_recall=1.0,
        minimum_macro_recall=1.0,
        cases=[
            ResearchRecallCase(
                case_id="stale-coordinates",
                query="port records",
                library_entry_ids=[entry.id],
                expected=[stale],
            )
        ],
    )

    with pytest.raises(ResearchRecallSuiteInvalid, match="coordinates do not match"):
        await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(suite)
