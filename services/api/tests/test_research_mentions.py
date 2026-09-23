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
    ResearchMentionKind,
    ResearchSelectionDenied,
    ResearchService,
)
from bukmatika.research.mentions import build_mentions


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


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
        work_title="Mention Work",
        edition_id=uuid4(),
        edition_title="Mention Edition",
        asset_id=uuid4(),
        document_id=document_id,
        chunk_id=uuid4(),
        section_id=section_id,
        section_ordinal=0,
        chunk_ordinal=0,
        heading="Mention section",
        locator={"page": 1},
        char_start=char_start,
        char_end=char_start + len(text),
        text=text,
        score=None,
    )


def _bundle(evidence: list[ResearchEvidenceItem]) -> ResearchEvidenceBundleResponse:
    reader = evidence[0]
    return ResearchEvidenceBundleResponse(
        question="Who, where, and what concepts appear?",
        reader=ReaderResearchContextRequest(
            library_entry_id=reader.library_entry_id,
            document_id=reader.document_id,
            section_id=reader.section_id,
            char_offset=reader.char_start,
        ),
        selected_library_entry_ids=[reader.library_entry_id],
        evidence=evidence,
    )


def test_mentions_classify_only_explicit_cues_and_preserve_ambiguity() -> None:
    text = (
        "Captain Christopher Columbus sailed toward the island of Hispaniola while discussing "
        "the doctrine of discovery. Christopher Columbus appears again without a title."
    )

    result = build_mentions(_bundle([_evidence(evidence_id="E1", text=text)]))

    assert [(item.text, item.kind) for item in result.items] == [
        ("Christopher Columbus", ResearchMentionKind.PERSON),
        ("Hispaniola", ResearchMentionKind.PLACE),
        ("discovery", ResearchMentionKind.CONCEPT),
        ("Christopher Columbus", ResearchMentionKind.AMBIGUOUS),
    ]
    assert [item.cue for item in result.items] == ["Captain", "island", "doctrine", None]
    for item in result.items:
        assert text[item.source_char_start : item.source_char_end] == item.text
        assert item.evidence_ids == ["E1"]


def test_mentions_do_not_promote_uncued_name_saint_prefix_or_titled_work() -> None:
    text = (
        "Christopher Columbus met Saint Augustine near St Louis. "
        "King James Bible appeared later."
    )

    result = build_mentions(_bundle([_evidence(evidence_id="E1", text=text)]))

    assert all(item.kind is ResearchMentionKind.AMBIGUOUS for item in result.items)
    assert all(item.cue is None for item in result.items)
    assert any(item.text == "Christopher Columbus" for item in result.items)
    assert any(item.text == "King James Bible" for item in result.items)
    assert not any(item.kind is ResearchMentionKind.PERSON for item in result.items)


def test_unquoted_concept_cue_does_not_swallow_following_prose() -> None:
    text = (
        "The doctrine of discovery shaped policy. "
        "The concept called “separation of powers” followed."
    )

    result = build_mentions(_bundle([_evidence(evidence_id="E1", text=text)]))
    concepts = [item for item in result.items if item.kind is ResearchMentionKind.CONCEPT]

    assert [(item.text, item.cue) for item in concepts] == [
        ("discovery", "doctrine"),
        ("separation of powers", "concept"),
    ]
    assert all("shaped" not in item.text for item in concepts)


def test_mentions_merge_same_canonical_span_across_overlapping_evidence() -> None:
    text = "The log names Captain Christopher Columbus before dawn."
    document_id = uuid4()
    section_id = uuid4()
    mention_start = text.index("Christopher")
    full = _evidence(
        evidence_id="E1",
        text=text,
        document_id=document_id,
        section_id=section_id,
    )
    selected_start = text.index("Captain")
    selected = _evidence(
        evidence_id="E2",
        text=text[selected_start:],
        document_id=document_id,
        section_id=section_id,
        char_start=selected_start,
    )
    selected.library_entry_id = full.library_entry_id

    result = build_mentions(_bundle([full, selected]))
    people = [item for item in result.items if item.kind is ResearchMentionKind.PERSON]

    assert len(people) == 1
    assert people[0].text == "Christopher Columbus"
    assert people[0].evidence_ids == ["E1", "E2"]
    assert people[0].source_char_start == mention_start
    assert people[0].source_char_end == mention_start + len("Christopher Columbus")


def test_mentions_bound_output_without_merging_distinct_source_spans() -> None:
    text = " ".join(f"Dr Ada Lovelace entry{i}." for i in range(100))

    result = build_mentions(_bundle([_evidence(evidence_id="E1", text=text)]))

    assert len(result.items) == 80
    assert result.truncated is True
    assert all(item.text == "Ada Lovelace" for item in result.items)
    assert len({(item.source_char_start, item.source_char_end) for item in result.items}) == 80


async def _seed_mentions_book(
    session: AsyncSession,
    *,
    principal: Principal,
) -> tuple[LibraryEntry, Document, DocumentSection]:
    text = "Captain Ada Lovelace wrote about the concept of analysis in the city of London."
    token = uuid4().hex
    work = Work(
        canonical_title="Mention Canonical Work",
        normalized_title="mention canonical work",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/mentions/{token}",
        byte_size=len(text.encode("utf-8")),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title="Mention Canonical Edition",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url="https://example.org/mentions.txt",
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
        heading="Analytical engine",
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


async def test_mentions_reuse_principal_scoped_canonical_evidence(session: AsyncSession) -> None:
    owner = Principal(kind="local", external_subject="mentions-owner")
    intruder = Principal(kind="local", external_subject="mentions-intruder")
    session.add_all([owner, intruder])
    await session.flush()
    entry, document, section = await _seed_mentions_book(session, principal=owner)
    request = ResearchEvidenceBundleRequest(
        question="Who and where are discussed?",
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

    result = await service.mentions(principal_id=owner.id, request=request)

    by_kind = {item.kind: item for item in result.items}
    assert by_kind[ResearchMentionKind.PERSON].text == "Ada Lovelace"
    assert by_kind[ResearchMentionKind.PLACE].text == "London"
    assert by_kind[ResearchMentionKind.CONCEPT].text == "analysis"
    for item in result.items:
        assert item.document_id == document.id
        assert item.section_id == section.id
        assert section.text[item.source_char_start : item.source_char_end] == item.text
        assert item.evidence_ids == ["E1"]

    with pytest.raises(ResearchSelectionDenied):
        await service.mentions(principal_id=intruder.id, request=request)


def test_research_mentions_route_is_publicly_mounted() -> None:
    schema = app.openapi()
    assert "/v1/research/mentions" in schema["paths"]
    assert "post" in schema["paths"]["/v1/research/mentions"]
