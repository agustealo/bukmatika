from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
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
from bukmatika.research import (
    GroundedAnswerClaim,
    GroundedResearchAnswer,
    ReaderResearchContextRequest,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceReferenceInvalid,
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


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"grounding-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def _seed_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    text: str,
    chunk_ranges: list[tuple[int, int]] | None = None,
) -> tuple[LibraryEntry, Document, DocumentSection, list[DocumentChunk]]:
    token = uuid4().hex
    work = Work(
        canonical_title=f"Grounding Work {suffix}",
        normalized_title=f"grounding work {suffix}",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/grounding/{suffix}/{token}",
        byte_size=max(1, len(text.encode("utf-8"))),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Grounding Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/grounding/{suffix}.txt",
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

    ranges = chunk_ranges or [(0, len(text))]
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
    return entry, document, section, chunks


async def test_evidence_bundle_binds_reader_selection_and_selected_owned_books(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "bundle")
    current_text = "Alpha navigation evidence. Beta navigation trade evidence."
    split = current_text.index("Beta")
    current, document, section, current_chunks = await _seed_book(
        session,
        principal=principal,
        suffix="current",
        text=current_text,
        chunk_ranges=[(0, split), (split, len(current_text))],
    )
    related, _, _, related_chunks = await _seed_book(
        session,
        principal=principal,
        suffix="related",
        text="Navigation evidence from a second selected source describes Atlantic trade routes.",
    )
    unselected, _, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="unselected",
        text="Navigation evidence navigation evidence navigation evidence from an unselected source.",
    )

    selection_start = 6
    selection_end = len(current_text) - 10
    request = ResearchEvidenceBundleRequest(
        question="What navigation evidence discusses trade?",
        reader=ReaderResearchContextRequest(
            library_entry_id=current.id,
            document_id=document.id,
            section_id=section.id,
            char_offset=selection_start,
            selection_start=selection_start,
            selection_end=selection_end,
        ),
        library_entry_ids=[current.id, related.id],
        related_limit=10,
    )
    service = ResearchService(session_scope_factory=_scope(session))

    bundle = await service.evidence_bundle(principal_id=principal.id, request=request)

    assert bundle.question == "What navigation evidence discusses trade?"
    assert bundle.selected_library_entry_ids == [current.id, related.id]
    assert [item.evidence_id for item in bundle.evidence] == [
        f"E{index}" for index in range(1, len(bundle.evidence) + 1)
    ]
    assert bundle.evidence[0].source_kind is ResearchEvidenceSourceKind.READER_SELECTION
    assert bundle.evidence[1].source_kind is ResearchEvidenceSourceKind.READER_SELECTION
    assert [item.chunk_id for item in bundle.evidence[:2]] == [
        current_chunks[0].id,
        current_chunks[1].id,
    ]
    assert bundle.evidence[0].char_start == selection_start
    assert bundle.evidence[1].char_end == selection_end
    assert "".join(item.text for item in bundle.evidence[:2]) == current_text[
        selection_start:selection_end
    ]

    selected_ids = {item.library_entry_id for item in bundle.evidence}
    assert selected_ids.issubset({current.id, related.id})
    assert unselected.id not in selected_ids
    assert related.id in selected_ids
    related_item = next(item for item in bundle.evidence if item.library_entry_id == related.id)
    assert related_item.chunk_id == related_chunks[0].id
    assert related_item.source_kind is ResearchEvidenceSourceKind.RELATED_PASSAGE
    assert related_item.score is not None
    assert related_item.score > 0

    assert len({item.chunk_id for item in bundle.evidence}) == len(bundle.evidence)
    for item in bundle.evidence:
        assert item.work_id
        assert item.edition_id
        assert item.asset_id
        assert item.document_id
        assert item.section_id
        assert item.chunk_id
        assert item.char_end > item.char_start
        assert item.text

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
    assert event.entity_id == document.id
    assert event.context["library_entry_id"] == str(current.id)
    assert event.context["selected_library_entry_ids"] == [str(current.id), str(related.id)]
    assert request.question not in event.context.values()
    assert current_text[selection_start:selection_end] not in event.context.values()


async def test_evidence_bundle_is_deterministic_and_deduplicates_reader_search_overlap(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "deterministic")
    text = "Navigation evidence about ocean trade and Atlantic routes."
    entry, document, section, chunks = await _seed_book(
        session,
        principal=principal,
        suffix="deterministic",
        text=text,
    )
    request = ResearchEvidenceBundleRequest(
        question="navigation evidence",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry.id,
            document_id=document.id,
            section_id=section.id,
            char_offset=4,
        ),
        library_entry_ids=[entry.id],
        related_limit=10,
    )
    service = ResearchService(session_scope_factory=_scope(session))

    first = await service.evidence_bundle(principal_id=principal.id, request=request)
    second = await service.evidence_bundle(principal_id=principal.id, request=request)

    assert [(item.evidence_id, item.chunk_id) for item in first.evidence] == [
        ("E1", chunks[0].id)
    ]
    assert [(item.evidence_id, item.chunk_id) for item in second.evidence] == [
        ("E1", chunks[0].id)
    ]
    assert first.evidence[0].source_kind is ResearchEvidenceSourceKind.READER_POSITION
    assert first.evidence[0].score is None


async def test_evidence_bundle_rejects_cross_principal_reader_context(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "owner")
    intruder = await _principal(session, "intruder")
    entry, document, section, _ = await _seed_book(
        session,
        principal=owner,
        suffix="private",
        text="Private navigation evidence belongs only to the owner.",
    )

    with pytest.raises(ResearchSelectionDenied, match="unavailable"):
        await ResearchService(session_scope_factory=_scope(session)).evidence_bundle(
            principal_id=intruder.id,
            request=ResearchEvidenceBundleRequest(
                question="What does this passage say?",
                reader=ReaderResearchContextRequest(
                    library_entry_id=entry.id,
                    document_id=document.id,
                    section_id=section.id,
                    char_offset=0,
                ),
                library_entry_ids=[entry.id],
                related_limit=0,
            ),
        )


async def test_evidence_bundle_rejects_invalid_reader_coordinates(session: AsyncSession) -> None:
    principal = await _principal(session, "invalid-position")
    entry, document, section, _ = await _seed_book(
        session,
        principal=principal,
        suffix="position",
        text="Canonical text with one valid reader section.",
    )
    service = ResearchService(session_scope_factory=_scope(session))

    with pytest.raises(ResearchReaderPositionInvalid, match="section"):
        await service.evidence_bundle(
            principal_id=principal.id,
            request=ResearchEvidenceBundleRequest(
                question="What is here?",
                reader=ReaderResearchContextRequest(
                    library_entry_id=entry.id,
                    document_id=document.id,
                    section_id=uuid4(),
                    char_offset=0,
                ),
                library_entry_ids=[entry.id],
                related_limit=0,
            ),
        )

    with pytest.raises(ResearchReaderPositionInvalid, match="offset"):
        await service.evidence_bundle(
            principal_id=principal.id,
            request=ResearchEvidenceBundleRequest(
                question="What is here?",
                reader=ReaderResearchContextRequest(
                    library_entry_id=entry.id,
                    document_id=document.id,
                    section_id=section.id,
                    char_offset=len(section.text) + 1,
                ),
                library_entry_ids=[entry.id],
                related_limit=0,
            ),
        )


def test_grounded_answer_accepts_known_evidence_and_rejects_fabricated_ids() -> None:
    entry_id = uuid4()
    document_id = uuid4()
    section_id = uuid4()
    common = {
        "library_entry_id": entry_id,
        "work_id": uuid4(),
        "work_title": "Work",
        "edition_id": uuid4(),
        "edition_title": "Edition",
        "asset_id": uuid4(),
        "document_id": document_id,
        "chunk_id": uuid4(),
        "section_id": section_id,
        "section_ordinal": 0,
        "chunk_ordinal": 0,
        "heading": "Heading",
        "locator": {"page": 1},
        "char_start": 0,
        "char_end": 12,
        "text": "Grounded text",
        "score": None,
    }
    from bukmatika.research import ResearchEvidenceBundleResponse, ResearchEvidenceItem

    bundle = ResearchEvidenceBundleResponse(
        question="Question",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry_id,
            document_id=document_id,
            section_id=section_id,
            char_offset=0,
        ),
        selected_library_entry_ids=[entry_id],
        evidence=[
            ResearchEvidenceItem(
                evidence_id="E1",
                source_kind=ResearchEvidenceSourceKind.READER_POSITION,
                **common,
            )
        ],
    )
    valid = GroundedResearchAnswer(
        answer="The source supports this statement.",
        claims=[GroundedAnswerClaim(text="Supported statement", evidence_ids=["E1"])],
    )
    assert ResearchService.validate_grounded_answer(bundle=bundle, answer=valid) is valid

    fabricated = GroundedResearchAnswer(
        answer="This cites evidence that is not in the bundle.",
        claims=[GroundedAnswerClaim(text="Unsupported statement", evidence_ids=["E2"])],
    )
    with pytest.raises(ResearchEvidenceReferenceInvalid, match="E2"):
        ResearchService.validate_grounded_answer(bundle=bundle, answer=fabricated)

    with pytest.raises(ValidationError):
        GroundedAnswerClaim(text="Malformed ID", evidence_ids=["E00"])


def test_grounding_request_validation_is_bounded() -> None:
    entry_id = uuid4()
    reader = {
        "library_entry_id": entry_id,
        "document_id": uuid4(),
        "section_id": uuid4(),
        "char_offset": 0,
    }
    with pytest.raises(ValidationError, match="current reader book"):
        ResearchEvidenceBundleRequest(
            question="question",
            reader=reader,
            library_entry_ids=[uuid4()],
        )

    with pytest.raises(ValidationError, match="start and end"):
        ReaderResearchContextRequest(
            **reader,
            selection_start=0,
        )

    with pytest.raises(ValidationError, match="character budget"):
        ReaderResearchContextRequest(
            **reader,
            selection_start=0,
            selection_end=6_001,
        )


def test_research_evidence_route_is_mounted() -> None:
    assert "/v1/research/evidence" in set(app.openapi()["paths"])
