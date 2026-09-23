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
    ResearchReaderPositionInvalid,
    ResearchSelectionDenied,
    ResearchService,
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
    chunk_ranges: list[tuple[int, int]],
) -> tuple[LibraryEntry, Document, DocumentSection, list[DocumentChunk], ReadingState]:
    token = uuid4().hex
    work = Work(
        canonical_title=f"Highlight Work {suffix}",
        normalized_title=f"highlight work {suffix}",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/highlight-research/{suffix}/{token}",
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
        remote_url=f"https://example.org/highlight/{suffix}.txt",
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
        chunk_count=len(chunk_ranges),
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
    for ordinal, (start, end) in enumerate(chunk_ranges):
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
    state = ReadingState(
        library_entry_id=entry.id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.25,
        section_id=section.id,
        section_ordinal=section.ordinal,
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
    note: str | None = None,
) -> Highlight:
    highlight = Highlight(
        reading_state_id=state.id,
        section_id=section.id,
        char_start=start,
        char_end=end,
        locator={"page": 999, "stale": "annotation snapshot"},
        note=note,
    )
    session.add(highlight)
    await session.flush()
    return highlight


async def test_selected_highlight_becomes_exact_canonical_evidence(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="highlight-evidence-owner")
    session.add(principal)
    await session.flush()
    text = "Alpha canonical passage about navigation and transatlantic trade evidence."
    split = text.index("navigation") + 5
    entry, document, section, chunks, state = await _seed_book(
        session,
        principal=principal,
        suffix="canonical",
        text=text,
        chunk_ranges=[(0, split), (split, len(text))],
    )
    start = text.index("canonical")
    end = text.index(" evidence")
    note = "My interpretation should never become quoted source evidence."
    highlight = await _highlight(
        session,
        state=state,
        section=section,
        start=start,
        end=end,
        note=note,
    )

    bundle = await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
        principal_id=principal.id,
        request=ResearchEvidenceBundleRequest(
            question="What does my selected evidence say?",
            reader=ReaderResearchContextRequest(
                library_entry_id=entry.id,
                document_id=document.id,
                section_id=section.id,
                char_offset=0,
            ),
            library_entry_ids=[entry.id],
            highlight_ids=[highlight.id],
            related_limit=0,
        ),
    )

    selected = [
        item
        for item in bundle.evidence
        if item.source_kind is ResearchEvidenceSourceKind.HIGHLIGHT_SELECTION
    ]
    assert len(selected) == 2
    assert [item.chunk_id for item in selected] == [chunk.id for chunk in chunks]
    assert {item.highlight_id for item in selected} == {highlight.id}
    assert "".join(item.text for item in selected) == section.text[start:end]
    assert selected[0].char_start == start
    assert selected[-1].char_end == end
    assert all(item.locator == section.locator for item in selected)
    assert all(item.locator != highlight.locator for item in selected)
    assert note not in " ".join(item.text for item in bundle.evidence)

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
    assert event.context["highlight_evidence_count"] == 2
    assert note not in event.context.values()
    assert section.text[start:end] not in event.context.values()


async def test_selected_highlight_must_belong_to_principal_and_selected_books(
    session: AsyncSession,
) -> None:
    owner = Principal(kind="local", external_subject="highlight-owner")
    researcher = Principal(kind="local", external_subject="highlight-researcher")
    session.add_all([owner, researcher])
    await session.flush()
    owner_entry, _, owner_section, _, owner_state = await _seed_book(
        session,
        principal=owner,
        suffix="owner",
        text="Owner-only canonical highlight evidence.",
        chunk_ranges=[(0, len("Owner-only canonical highlight evidence."))],
    )
    foreign_highlight = await _highlight(
        session,
        state=owner_state,
        section=owner_section,
        start=0,
        end=10,
    )
    entry, document, section, _, _ = await _seed_book(
        session,
        principal=researcher,
        suffix="researcher",
        text="Researcher's valid current reader evidence.",
        chunk_ranges=[(0, len("Researcher's valid current reader evidence."))],
    )

    with pytest.raises(ResearchSelectionDenied, match="highlights are unavailable"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=researcher.id,
            request=ResearchEvidenceBundleRequest(
                question="Can I use that highlight?",
                reader=ReaderResearchContextRequest(
                    library_entry_id=entry.id,
                    document_id=document.id,
                    section_id=section.id,
                    char_offset=0,
                ),
                library_entry_ids=[entry.id],
                highlight_ids=[foreign_highlight.id],
                related_limit=0,
            ),
        )

    assert owner_entry.id != entry.id


async def test_selected_highlight_outside_selected_owned_book_is_denied(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="highlight-selection-owner")
    session.add(principal)
    await session.flush()
    current, document, section, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="current",
        text="Current reader evidence.",
        chunk_ranges=[(0, len("Current reader evidence."))],
    )
    other, _, other_section, _, other_state = await _seed_book(
        session,
        principal=principal,
        suffix="other",
        text="Other owned highlight evidence.",
        chunk_ranges=[(0, len("Other owned highlight evidence."))],
    )
    other_highlight = await _highlight(
        session,
        state=other_state,
        section=other_section,
        start=0,
        end=10,
    )

    with pytest.raises(ResearchSelectionDenied, match="highlights are unavailable"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=principal.id,
            request=ResearchEvidenceBundleRequest(
                question="Keep the research scope explicit.",
                reader=ReaderResearchContextRequest(
                    library_entry_id=current.id,
                    document_id=document.id,
                    section_id=section.id,
                    char_offset=0,
                ),
                library_entry_ids=[current.id],
                highlight_ids=[other_highlight.id],
                related_limit=0,
            ),
        )

    assert other.id != current.id


async def test_stale_highlight_coordinates_fail_closed(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="highlight-stale-owner")
    session.add(principal)
    await session.flush()
    text = "Canonical text remains the source authority."
    entry, document, section, _, state = await _seed_book(
        session,
        principal=principal,
        suffix="stale",
        text=text,
        chunk_ranges=[(0, len(text))],
    )
    highlight = await _highlight(
        session,
        state=state,
        section=section,
        start=1,
        end=len(text) + 10,
    )

    with pytest.raises(ResearchReaderPositionInvalid, match="coordinates"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=principal.id,
            request=ResearchEvidenceBundleRequest(
                question="Do not clip stale coordinates.",
                reader=ReaderResearchContextRequest(
                    library_entry_id=entry.id,
                    document_id=document.id,
                    section_id=section.id,
                    char_offset=0,
                ),
                library_entry_ids=[entry.id],
                highlight_ids=[highlight.id],
                related_limit=0,
            ),
        )


def test_highlight_selection_request_is_explicit_unique_and_bounded() -> None:
    entry_id = uuid4()
    highlight_id = uuid4()
    common = {
        "question": "question",
        "reader": {
            "library_entry_id": entry_id,
            "document_id": uuid4(),
            "section_id": uuid4(),
            "char_offset": 0,
        },
        "library_entry_ids": [entry_id],
        "related_limit": 0,
    }
    request = ResearchEvidenceBundleRequest(**common)
    assert request.highlight_ids == []

    with pytest.raises(ValidationError, match="Selected highlights must be unique"):
        ResearchEvidenceBundleRequest(**common, highlight_ids=[highlight_id, highlight_id])

    with pytest.raises(ValidationError):
        ResearchEvidenceBundleRequest(**common, highlight_ids=[uuid4() for _ in range(9)])
