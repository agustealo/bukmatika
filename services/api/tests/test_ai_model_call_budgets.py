import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_planning import RecordingGateway as PlanningGateway
from test_ai_planning import _research_plan_payload
from test_grounded_synthesis import (
    _RecordingGateway as GroundedGateway,
)
from test_grounded_synthesis import _principal, _request, _scope, _seed_book

from bukmatika.ai.gateway import ModelProviderNotReady, ModelReadinessState
from bukmatika.ai.research_domain import GroundedResearchSelectionRequest
from bukmatika.ai.research_service import (
    GroundedResearchSynthesisService,
    ResearchSelectionEvidenceUnavailable,
)
from bukmatika.ai.service import AIDisabled, PlanningService
from bukmatika.persistence.personalization_models import UserModel
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import ContextRequest, ContextTask
from bukmatika.research import ResearchService


async def test_explicit_planning_request_has_one_generation_call_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-plan")
    scope = _scope(session)
    gateway = PlanningGateway(_research_plan_payload())
    service = PlanningService(
        gateway=gateway,
        context_assembler=ContextAssembler(session_scope_factory=scope),
        session_scope_factory=scope,
    )

    result = await service.propose(
        principal_id=principal.id,
        user_request="Search my selected research context and open the best source.",
        context_request=ContextRequest(task=ContextTask.RESEARCH),
    )

    assert result.status == "proposed"
    assert len(gateway.calls) == 1


async def test_ai_disabled_planning_has_zero_generation_call_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-plan-disabled")
    model = await session.scalar(select(UserModel).where(UserModel.principal_id == principal.id))
    assert model is not None
    model.ai_enabled = False
    await session.flush()

    scope = _scope(session)
    gateway = PlanningGateway(_research_plan_payload())
    service = PlanningService(
        gateway=gateway,
        context_assembler=ContextAssembler(session_scope_factory=scope),
        session_scope_factory=scope,
    )

    with pytest.raises(AIDisabled):
        await service.propose(
            principal_id=principal.id,
            user_request="Search my books for navigation evidence.",
            context_request=ContextRequest(task=ContextTask.RESEARCH),
        )

    assert gateway.calls == []


async def test_grounded_answer_has_one_readiness_and_one_generation_call_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-answer")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="budget-answer",
        text="Documented Atlantic navigation relied on routes and recorded bearings.",
    )
    scope = _scope(session)
    gateway = GroundedGateway()
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    result = await service.answer(
        principal_id=principal.id,
        request=_request(entry, document, section),
    )

    assert result.answer.claims
    assert gateway.readiness_calls == 1
    assert len(gateway.calls) == 1


async def test_selected_book_answer_keeps_lexical_presearch_inside_one_model_call_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-selection")
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="budget-selection",
        text="Documented Atlantic navigation relied on routes and recorded bearings.",
    )
    scope = _scope(session)
    gateway = GroundedGateway()
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

    assert result.evidence.evidence
    assert gateway.readiness_calls == 1
    assert len(gateway.calls) == 1


async def test_selected_book_no_match_has_zero_model_runtime_call_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-selection-no-match")
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="budget-selection-no-match",
        text="Orchards produced apples and pears during the autumn harvest.",
    )
    scope = _scope(session)
    gateway = GroundedGateway()
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


async def test_not_ready_grounded_answer_has_one_probe_and_zero_generation_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-not-ready")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="budget-not-ready",
        text="Canonical research evidence remains available while the model is offline.",
    )
    scope = _scope(session)
    gateway = GroundedGateway(readiness_state=ModelReadinessState.PROVIDER_UNREACHABLE)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(ModelProviderNotReady):
        await service.answer(
            principal_id=principal.id,
            request=_request(entry, document, section),
        )

    assert gateway.readiness_calls == 1
    assert gateway.calls == []


async def test_ai_disabled_grounded_answer_has_zero_probe_and_generation_budget(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "budget-answer-disabled")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="budget-answer-disabled",
        text="Canonical evidence remains available without model execution.",
    )
    model = await session.scalar(select(UserModel).where(UserModel.principal_id == principal.id))
    assert model is not None
    model.ai_enabled = False
    await session.flush()

    scope = _scope(session)
    gateway = GroundedGateway()
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(AIDisabled):
        await service.answer(
            principal_id=principal.id,
            request=_request(entry, document, section),
        )

    assert gateway.readiness_calls == 0
    assert gateway.calls == []
