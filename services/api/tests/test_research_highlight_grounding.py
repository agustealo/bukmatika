from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.reader_models import Highlight, ReadingState
from bukmatika.research import (
    ReaderResearchContextRequest,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceSourceKind,
    ResearchSelectionDenied,
    ResearchService,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"highlight-grounding-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def _seed_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    text: str,
    split: int | None = None,
) -> tuple[LibraryEntry, Document, DocumentSection, list[DocumentChunk], ReadingState]:
    token = uuid4().hex
    work = Work(
        canonical_title=f"Highlight Work {suffix}",
        normalized_title=f"highlight work {suffix}",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/highlight-grounding/{suffix}/{token}",
        byte_size=max(1, len(text.encode("utf-8"))),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Highlight Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/highlight-grounding/{suffix}.txt",
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

    ranges = [(0, len(text))] if split is None else [(0, split), (split, len(text))]
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="1",
        section_count=1,
        chunk_count=len(ranges),
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

    chunks: list[DocumentChunk] = []
    for ordinal, (start, end) in enumerate(ranges):
        chunk = DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=ordinal,
            char_start=start,
            char_end=end,
            text=text[start:end],
        )
        session.add(chunk)
        chunks.append(chunk)
    await session.flush()

    state = ReadingState(
        library_entry_id=entry.id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.25,
        section_id=section.id,
        section_ordinal=0,
        char_offset=0,
        locator=section.locator,
    )
    session.add(state)
    await session.flush()
    return entry, document, section, chunks, state


async def _highlight(
    session: AsyncSession,
    *,
    state: ReadingState,
    section: DocumentSection,
    start: int,
    end: int,
    note: str = "Private note that must not become research evidence",
) -> Highlight:
    highlight = Highlight(
        reading_state_id=state.id,
        section_id=section.id,
        char_start=start,
        char_end=end,
        locator=section.locator,
        note=note,
    )
    session.add(highlight)
    await session.flush()
    return highlight


async def test_selected_highlight_uses_canonical_section_text_and_survives_chunk_overlap(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "canonical")
    text = "Alpha canonical highlight crosses the chunk seam and preserves exact provenance."
    split = text.index("the chunk")
    entry, document, section, chunks, state = await _seed_book(
        session,
        principal=principal,
        suffix="canonical",
        text=text,
        split=split,
    )
    start = text.index("canonical")
    end = text.index("provenance") + len("provenance")
    highlight = await _highlight(
        session,
        state=state,
        section=section,
        start=start,
        end=end,
    )

    # Poison the denormalized chunk text after coordinates are established. Highlight evidence
    # must still be sliced from canonical DocumentSection.text, never copied from a request or note.
    chunks[0].text = "X" * len(chunks[0].text)
    await session.flush()

    request = ResearchEvidenceBundleRequest(
        question="What does my selected highlight establish?",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry.id,
            document_id=document.id,
            section_id=section.id,
            char_offset=0,
        ),
        library_entry_ids=[entry.id],
        selected_highlight_ids=[highlight.id],
        related_limit=0,
    )
    bundle = await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
        principal_id=principal.id,
        request=request,
    )

    assert bundle.evidence[0].source_kind is ResearchEvidenceSourceKind.READER_POSITION
    selected = [
        item
        for item in bundle.evidence
        if item.source_kind is ResearchEvidenceSourceKind.SELECTED_HIGHLIGHT
    ]
    assert len(selected) == 2
    assert [item.chunk_id for item in selected] == [chunks[0].id, chunks[1].id]
    assert selected[0].chunk_id == bundle.evidence[0].chunk_id
    assert all(item.source_highlight_id == highlight.id for item in selected)
    assert "".join(item.text for item in selected) == text[start:end]
    assert selected[0].text != chunks[0].text
    assert selected[0].library_entry_id == entry.id
    assert selected[0].document_id == document.id
    assert selected[0].section_id == section.id
    assert selected[0].locator == section.locator
    assert all(item.score is None for item in selected)

    event = await session.scalar(
        select(InteractionEvent)
        .where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "research.evidence_built",
        )
        .order_by(InteractionEvent.occurred_at.desc(), InteractionEvent.id.desc())
        .limit(1)
    )
    assert event is not None
    assert event.context["selected_highlight_ids"] == [str(highlight.id)]
    assert event.context["selected_highlight_count"] == 1
    assert event.context["highlight_evidence_count"] == 2
    assert highlight.note not in event.context.values()
    assert text[start:end] not in event.context.values()


async def test_selected_highlight_is_principal_scoped(session: AsyncSession) -> None:
    owner = await _principal(session, "owner")
    intruder = await _principal(session, "intruder")
    owner_entry, _, owner_section, _, owner_state = await _seed_book(
        session,
        principal=owner,
        suffix="owner",
        text="Owner-only highlighted evidence.",
    )
    owner_highlight = await _highlight(
        session,
        state=owner_state,
        section=owner_section,
        start=0,
        end=10,
    )
    intruder_entry, intruder_document, intruder_section, _, _ = await _seed_book(
        session,
        principal=intruder,
        suffix="intruder",
        text="Intruder reader context is valid for the intruder.",
    )

    request = ResearchEvidenceBundleRequest(
        question="Can I use that highlight?",
        reader=ReaderResearchContextRequest(
            library_entry_id=intruder_entry.id,
            document_id=intruder_document.id,
            section_id=intruder_section.id,
            char_offset=0,
        ),
        library_entry_ids=[intruder_entry.id],
        selected_highlight_ids=[owner_highlight.id],
        related_limit=0,
    )

    with pytest.raises(ResearchSelectionDenied, match="highlight"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=intruder.id,
            request=request,
        )

    assert owner_entry.id != intruder_entry.id


async def test_selected_highlight_must_belong_to_explicit_research_books(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "selection")
    current, current_document, current_section, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="current",
        text="Current selected source.",
    )
    other, _, other_section, _, other_state = await _seed_book(
        session,
        principal=principal,
        suffix="other",
        text="Owned but unselected highlighted source.",
    )
    other_highlight = await _highlight(
        session,
        state=other_state,
        section=other_section,
        start=0,
        end=12,
    )

    request = ResearchEvidenceBundleRequest(
        question="Can an unselected book leak into evidence?",
        reader=ReaderResearchContextRequest(
            library_entry_id=current.id,
            document_id=current_document.id,
            section_id=current_section.id,
            char_offset=0,
        ),
        library_entry_ids=[current.id],
        selected_highlight_ids=[other_highlight.id],
        related_limit=0,
    )

    with pytest.raises(ResearchSelectionDenied, match="highlight"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=principal.id,
            request=request,
        )

    assert other.id not in request.library_entry_ids


async def test_evidence_selection_validates_all_books_even_without_related_search(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "book-owner")
    intruder = await _principal(session, "book-intruder")
    current, document, section, _, _ = await _seed_book(
        session,
        principal=owner,
        suffix="book-owner",
        text="Owned reader source.",
    )
    foreign, _, _, _, _ = await _seed_book(
        session,
        principal=intruder,
        suffix="book-intruder",
        text="Foreign selected source.",
    )

    request = ResearchEvidenceBundleRequest(
        question="Validate every selected book.",
        reader=ReaderResearchContextRequest(
            library_entry_id=current.id,
            document_id=document.id,
            section_id=section.id,
            char_offset=0,
        ),
        library_entry_ids=[current.id, foreign.id],
        related_limit=0,
    )

    with pytest.raises(ResearchSelectionDenied, match="library entries"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=owner.id,
            request=request,
        )


def test_selected_highlight_request_is_bounded_and_unique() -> None:
    entry_id = uuid4()
    reader = ReaderResearchContextRequest(
        library_entry_id=entry_id,
        document_id=uuid4(),
        section_id=uuid4(),
        char_offset=0,
    )
    duplicate = uuid4()

    with pytest.raises(ValidationError, match="highlights must be unique"):
        ResearchEvidenceBundleRequest(
            question="question",
            reader=reader,
            library_entry_ids=[entry_id],
            selected_highlight_ids=[duplicate, duplicate],
        )

    with pytest.raises(ValidationError):
        ResearchEvidenceBundleRequest(
            question="question",
            reader=reader,
            library_entry_ids=[entry_id],
            selected_highlight_ids=[uuid4() for _ in range(9)],
        )
