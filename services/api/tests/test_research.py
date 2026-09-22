import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.gateway import ModelProviderUnconfigured, ModelRequest, UnconfiguredModelGateway
from bukmatika.ai.service import AIDisabled
from bukmatika.config import Settings
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
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import PersonalizationSettingsUpdate
from bukmatika.personalization.service import PersonalizationService
from bukmatika.research import (
    GroundedAnswerStatus,
    GroundedCitationInvalid,
    GroundedResearchRequest,
    GroundedResearchService,
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


class _CapturingGroundedGateway:
    def __init__(self, draft: dict[str, object]) -> None:
        self._draft = draft
        self.requests: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return "test-provider"

    @property
    def model_name(self) -> str:
        return "test-model"

    async def generate_structured(self, request, response_type):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return response_type.model_validate(self._draft)


def _grounded_service(
    session: AsyncSession,
    *,
    gateway,  # type: ignore[no-untyped-def]
) -> GroundedResearchService:
    scope = _scope(session)
    return GroundedResearchService(
        gateway=gateway,
        research_service=ResearchService(session_scope_factory=scope),
        context_assembler=ContextAssembler(session_scope_factory=scope),
        settings=Settings(ai_provider="none"),
        session_scope_factory=scope,
    )


async def test_grounded_answer_cites_only_exact_retrieved_selected_book_evidence(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="grounded-owner")
    session.add(principal)
    await session.flush()
    entry, document, section, chunk, work, edition = await _seed_research_book(
        session,
        principal=principal,
        suffix="grounded-atlantic",
        text="Maritime trade crossed the Atlantic through documented commercial routes.",
    )
    session.add(
        InteractionEvent(
            principal_id=principal.id,
            event_type="private.test",
            entity_type="document",
            entity_id=document.id,
            context={"private": "raw-personalization-secret"},
        )
    )
    await session.flush()

    gateway = _CapturingGroundedGateway(
        {
            "claims": [
                {
                    "text": "The selected source describes documented Atlantic trade routes.",
                    "evidence_ids": [str(chunk.id)],
                }
            ],
            "insufficient_evidence": False,
        }
    )
    response = await _grounded_service(session, gateway=gateway).answer(
        principal_id=principal.id,
        request=GroundedResearchRequest(
            question="What does the source say about Atlantic maritime trade?",
            library_entry_ids=[entry.id],
        ),
    )

    assert response.status is GroundedAnswerStatus.GROUNDED
    assert response.provider == "test-provider"
    assert response.model == "test-model"
    assert response.retrieval_count == 1
    assert len(response.claims) == 1
    citation = response.claims[0].citations[0]
    assert citation.evidence_id == chunk.id
    assert citation.library_entry_id == entry.id
    assert citation.work_id == work.id
    assert citation.edition_id == edition.id
    assert citation.document_id == document.id
    assert citation.section_id == section.id
    assert citation.locator == {"page": 1, "section": "grounded-atlantic"}

    assert len(gateway.requests) == 1
    payload = gateway.requests[0].payload
    assert set(payload) == {"question", "preferences", "evidence"}
    serialized_payload = json.dumps(payload, default=str)
    assert "raw-personalization-secret" not in serialized_payload
    assert str(chunk.id) in serialized_payload
    assert str(document.id) not in serialized_payload

    event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "research.grounded_answer_completed",
        )
    )
    assert event is not None
    assert event.context["provider"] == "test-provider"
    assert event.context["model"] == "test-model"
    assert event.context["cited_evidence_ids"] == [str(chunk.id)]


async def test_grounded_answer_rejects_model_citation_outside_retrieved_evidence(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="grounded-bad-citation")
    session.add(principal)
    await session.flush()
    entry, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="bad-citation",
        text="Maritime trade evidence is available in this selected source.",
    )
    gateway = _CapturingGroundedGateway(
        {
            "claims": [
                {
                    "text": "This claim tries to cite evidence the model never received.",
                    "evidence_ids": [str(uuid4())],
                }
            ],
            "insufficient_evidence": False,
        }
    )

    with pytest.raises(GroundedCitationInvalid, match="outside"):
        await _grounded_service(session, gateway=gateway).answer(
            principal_id=principal.id,
            request=GroundedResearchRequest(
                question="What does the maritime trade evidence show?",
                library_entry_ids=[entry.id],
            ),
        )


async def test_grounded_answer_normalizes_cross_principal_selection_denial(
    session: AsyncSession,
) -> None:
    owner = Principal(kind="local", external_subject="grounded-private-owner")
    intruder = Principal(kind="local", external_subject="grounded-private-intruder")
    session.add_all([owner, intruder])
    await session.flush()
    entry, _, _, chunk, _, _ = await _seed_research_book(
        session,
        principal=owner,
        suffix="grounded-private",
        text="Private maritime evidence belongs only to the owner.",
    )
    gateway = _CapturingGroundedGateway(
        {
            "claims": [
                {"text": "Private claim", "evidence_ids": [str(chunk.id)]}
            ],
            "insufficient_evidence": False,
        }
    )

    with pytest.raises(ResearchSelectionDenied, match="unavailable"):
        await _grounded_service(session, gateway=gateway).answer(
            principal_id=intruder.id,
            request=GroundedResearchRequest(
                question="private maritime evidence",
                library_entry_ids=[entry.id],
            ),
        )
    assert gateway.requests == []


async def test_ai_disabled_blocks_synthesis_but_not_lexical_research(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="grounded-ai-off")
    session.add(principal)
    await session.flush()
    entry, _, _, chunk, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="ai-off",
        text="Maritime trade remains searchable without AI synthesis.",
    )
    scope = _scope(session)
    personalization = PersonalizationService(session_scope_factory=scope)
    await personalization.update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=True,
            autonomy_level=0,
        ),
    )
    gateway = _CapturingGroundedGateway(
        {
            "claims": [{"text": "Never called", "evidence_ids": [str(chunk.id)]}],
            "insufficient_evidence": False,
        }
    )

    with pytest.raises(AIDisabled):
        await _grounded_service(session, gateway=gateway).answer(
            principal_id=principal.id,
            request=GroundedResearchRequest(
                question="maritime trade",
                library_entry_ids=[entry.id],
            ),
        )
    assert gateway.requests == []

    lexical = await ResearchService(session_scope_factory=scope).search(
        principal_id=principal.id,
        request=ResearchSearchRequest(
            query="maritime trade",
            library_entry_ids=[entry.id],
        ),
    )
    assert [passage.chunk_id for passage in lexical.passages] == [chunk.id]


async def test_unconfigured_provider_fails_closed_after_real_retrieval(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="grounded-unconfigured")
    session.add(principal)
    await session.flush()
    entry, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="unconfigured",
        text="Maritime trade evidence exists even when synthesis is not configured.",
    )

    with pytest.raises(ModelProviderUnconfigured):
        await _grounded_service(session, gateway=UnconfiguredModelGateway()).answer(
            principal_id=principal.id,
            request=GroundedResearchRequest(
                question="maritime trade evidence",
                library_entry_ids=[entry.id],
            ),
        )


async def test_no_retrieved_evidence_returns_without_model_call(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="grounded-no-evidence")
    session.add(principal)
    await session.flush()
    entry, _, _, chunk, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="no-evidence",
        text="Maritime trade is the only subject in this source.",
    )
    gateway = _CapturingGroundedGateway(
        {
            "claims": [{"text": "Never called", "evidence_ids": [str(chunk.id)]}],
            "insufficient_evidence": False,
        }
    )

    response = await _grounded_service(session, gateway=gateway).answer(
        principal_id=principal.id,
        request=GroundedResearchRequest(
            question="quantum semiconductor fabrication",
            library_entry_ids=[entry.id],
        ),
    )

    assert response.status is GroundedAnswerStatus.NO_EVIDENCE
    assert response.claims == []
    assert response.provider is None
    assert response.model is None
    assert gateway.requests == []


async def test_model_can_decline_when_retrieved_passages_do_not_support_answer(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="grounded-insufficient")
    session.add(principal)
    await session.flush()
    entry, _, _, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="insufficient",
        text="Maritime trade appears here, but the requested conclusion is not established.",
    )
    gateway = _CapturingGroundedGateway(
        {"claims": [], "insufficient_evidence": True}
    )

    response = await _grounded_service(session, gateway=gateway).answer(
        principal_id=principal.id,
        request=GroundedResearchRequest(
            question="What does maritime trade prove about an unsupported conclusion?",
            library_entry_ids=[entry.id],
        ),
    )

    assert response.status is GroundedAnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.claims == []
    assert response.provider == "test-provider"
    assert response.model == "test-model"
    assert len(gateway.requests) == 1
