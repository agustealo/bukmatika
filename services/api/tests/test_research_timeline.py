from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Principal, StoredObject, Work
from bukmatika.research import (
    ReaderResearchContextRequest,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceBundleResponse,
    ResearchEvidenceItem,
    ResearchEvidenceSourceKind,
    ResearchSelectionDenied,
    ResearchService,
    ResearchTimelinePrecision,
)
from bukmatika.research.timeline import build_timeline


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _bundle_with_evidence(evidence: list[ResearchEvidenceItem]) -> ResearchEvidenceBundleResponse:
    reader = evidence[0]
    return ResearchEvidenceBundleResponse(
        question="Build a chronology from these sources",
        reader=ReaderResearchContextRequest(
            library_entry_id=reader.library_entry_id,
            document_id=reader.document_id,
            section_id=reader.section_id,
            char_offset=reader.char_start,
        ),
        selected_library_entry_ids=[reader.library_entry_id],
        evidence=evidence,
    )


def _evidence(
    *,
    evidence_id: str,
    text: str,
    document_id: UUID | None = None,
    section_id: UUID | None = None,
    char_start: int = 0,
) -> ResearchEvidenceItem:
    document_id = document_id or uuid4()
    section_id = section_id or uuid4()
    return ResearchEvidenceItem(
        evidence_id=evidence_id,
        source_kind=ResearchEvidenceSourceKind.READER_POSITION,
        library_entry_id=uuid4(),
        work_id=uuid4(),
        work_title="Timeline Work",
        edition_id=uuid4(),
        edition_title="Timeline Edition",
        asset_id=uuid4(),
        document_id=document_id,
        chunk_id=uuid4(),
        section_id=section_id,
        section_ordinal=0,
        chunk_ordinal=0,
        heading="Timeline section",
        locator={"page": 1},
        char_start=char_start,
        char_end=char_start + len(text),
        text=text,
        score=None,
    )


def test_timeline_extracts_exact_dates_and_preserves_numeric_ambiguity() -> None:
    text = (
        "On October 12, 1492, the log records landfall. "
        "A later ledger says 10/11/1493 without declaring its date convention. "
        "In 1501 the route changed."
    )
    bundle = _bundle_with_evidence([_evidence(evidence_id="E1", text=text)])

    timeline = build_timeline(bundle)

    assert [item.year for item in timeline.items] == [1492, 1493, 1501]
    assert timeline.items[0].month == 10
    assert timeline.items[0].day == 12
    assert timeline.items[0].precision is ResearchTimelinePrecision.DAY
    assert timeline.items[0].date_label == "October 12, 1492"
    assert timeline.items[0].event_text in text
    assert timeline.items[1].precision is ResearchTimelinePrecision.AMBIGUOUS
    assert timeline.items[1].month is None
    assert timeline.items[1].day is None
    assert timeline.items[1].date_label == "10/11/1493"
    assert timeline.items[2].precision is ResearchTimelinePrecision.YEAR
    assert timeline.items[2].evidence_ids == ["E1"]
    assert timeline.undated_evidence_ids == []


def test_timeline_merges_the_same_canonical_date_span_across_overlapping_evidence() -> None:
    text = "Before dawn, October 12, 1492 was entered in the log."
    document_id = uuid4()
    section_id = uuid4()
    date_start = text.index("October")
    full = _evidence(
        evidence_id="E1",
        text=text,
        document_id=document_id,
        section_id=section_id,
    )
    selected_text = text[date_start:]
    selected = _evidence(
        evidence_id="E2",
        text=selected_text,
        document_id=document_id,
        section_id=section_id,
        char_start=date_start,
    )
    selected.library_entry_id = full.library_entry_id

    timeline = build_timeline(_bundle_with_evidence([full, selected]))

    assert len(timeline.items) == 1
    assert timeline.items[0].evidence_ids == ["E1", "E2"]
    assert timeline.items[0].source_char_start == date_start
    assert timeline.items[0].source_char_end == date_start + len("October 12, 1492")
    assert timeline.items[0].event_text in text


def test_timeline_surfaces_undated_evidence_and_bounds_output() -> None:
    dated = " ".join(str(year) for year in range(1000, 1100))
    first = _evidence(evidence_id="E1", text=dated)
    second = _evidence(evidence_id="E2", text="This evidence has no explicit supported date.")
    second.library_entry_id = first.library_entry_id

    timeline = build_timeline(_bundle_with_evidence([first, second]))

    assert len(timeline.items) == 80
    assert timeline.truncated is True
    assert timeline.undated_evidence_ids == ["E2"]
    assert timeline.items[0].date_label == "1000"
    assert timeline.items[-1].date_label == "1079"


async def _seed_timeline_book(
    session: AsyncSession,
    *,
    principal: Principal,
) -> tuple[LibraryEntry, Document, DocumentSection]:
    text = "On October 12, 1492, the canonical account records landfall."
    token = uuid4().hex
    work = Work(canonical_title="Timeline Canonical Work", normalized_title="timeline canonical work")
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/timeline/{token}",
        byte_size=len(text.encode("utf-8")),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title="Timeline Canonical Edition",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url="https://example.org/timeline.txt",
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
        heading="Voyage log",
        locator={"page": 1},
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
    return entry, document, section


async def test_timeline_reuses_principal_scoped_canonical_evidence(session: AsyncSession) -> None:
    owner = Principal(kind="local", external_subject="timeline-owner")
    intruder = Principal(kind="local", external_subject="timeline-intruder")
    session.add_all([owner, intruder])
    await session.flush()
    entry, document, section = await _seed_timeline_book(session, principal=owner)
    request = ResearchEvidenceBundleRequest(
        question="When was landfall recorded?",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry.id,
            document_id=document.id,
            section_id=section.id,
            char_offset=0,
        ),
        library_entry_ids=[entry.id],
        related_limit=0,
    )
    service = ResearchService(session_scope_factory=_scope(session))

    timeline = await service.timeline(principal_id=owner.id, request=request)

    assert len(timeline.items) == 1
    item = timeline.items[0]
    assert item.date_label == "October 12, 1492"
    assert item.evidence_ids == ["E1"]
    assert item.document_id == document.id
    assert item.section_id == section.id
    assert item.event_text == section.text
    assert timeline.evidence.evidence[0].text == section.text

    with pytest.raises(ResearchSelectionDenied):
        await service.timeline(principal_id=intruder.id, request=request)


def test_research_timeline_route_is_publicly_mounted() -> None:
    schema = app.openapi()
    assert "/v1/research/timeline" in schema["paths"]
    assert "post" in schema["paths"]["/v1/research/timeline"]
