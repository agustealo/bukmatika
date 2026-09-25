import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_grounded_synthesis import (
    _principal,
    _RecordingGateway,
    _scope,
    _seed_book,
)

from bukmatika.ai.research_domain import GroundedResearchSelectionRequest
from bukmatika.ai.research_service import (
    GroundedResearchSynthesisService,
    ResearchSelectionEvidenceUnavailable,
)
from bukmatika.research import ResearchSelectionDenied, ResearchService


async def test_selected_library_answer_derives_canonical_anchor_and_reuses_grounded_synthesis(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "selection-success")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="selection-success",
        text="Documented Atlantic navigation relied on routes and recorded bearings.",
    )
    gateway = _RecordingGateway()
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    result = await service.answer_selection(
        principal_id=principal.id,
        request=GroundedResearchSelectionRequest(
            question="What Atlantic navigation evidence is documented?",
            library_entry_ids=[entry.id],
            related_limit=4,
        ),
    )

    assert gateway.readiness_calls == 1
    assert len(gateway.calls) == 1
    assert result.evidence.selected_library_entry_ids == [entry.id]
    assert result.evidence.evidence
    anchor = result.evidence.evidence[0]
    assert anchor.source_kind.value == "reader_position"
    assert anchor.library_entry_id == entry.id
    assert anchor.document_id == document.id
    assert anchor.section_id == section.id
    assert anchor.char_start == 0
    assert result.answer.claims[0].evidence_ids == [anchor.evidence_id]


async def test_selected_library_answer_rejects_no_lexical_anchor_before_model_access(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "selection-no-match")
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="selection-no-match",
        text="Orchards produced apples and pears during the autumn harvest.",
    )
    gateway = _RecordingGateway()
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(ResearchSelectionEvidenceUnavailable):
        await service.answer_selection(
            principal_id=principal.id,
            request=GroundedResearchSelectionRequest(
                question="celestial navigation longitude stars",
                library_entry_ids=[entry.id],
            ),
        )

    assert gateway.readiness_calls == 0
    assert gateway.calls == []


async def test_selected_library_answer_rejects_cross_principal_scope_before_model_access(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "selection-owner")
    other = await _principal(session, "selection-other")
    entry, _, _ = await _seed_book(
        session,
        principal=owner,
        suffix="selection-owner",
        text="Atlantic navigation relied on recorded bearings.",
    )
    gateway = _RecordingGateway()
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(ResearchSelectionDenied):
        await service.answer_selection(
            principal_id=other.id,
            request=GroundedResearchSelectionRequest(
                question="What navigation evidence is documented?",
                library_entry_ids=[entry.id],
            ),
        )

    assert gateway.readiness_calls == 0
    assert gateway.calls == []
