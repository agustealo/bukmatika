from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai import (
    ActionDecisionValue,
    ActionPolicy,
    CapabilityName,
    CapabilityRegistry,
    PlanProposal,
    PlanStep,
)
from bukmatika.ai.execution import (
    AIExecutionDisabled,
    ActionApprovalRequired,
    ActionExecutionDenied,
    CapabilityArgumentsInvalid,
    CapabilityExecutorRegistry,
    CapabilityExecutorUnavailable,
    ExecutionCoordinator,
    ResearchSearchExecutor,
)
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.execution import ActionExecutionNotFound, PlanIntegrityError
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Principal, StoredObject, Work
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import ActionDecision, Plan
from bukmatika.persistence.plans import PlanRepository
from bukmatika.personalization.domain import ContextManifest, ContextTask
from bukmatika.research import ResearchService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"execution-{suffix}")
    session.add(principal)
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    return principal


async def _seed_research_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    text: str,
) -> tuple[LibraryEntry, Document, DocumentSection, DocumentChunk]:
    work = Work(
        canonical_title=f"Execution Work {suffix}",
        normalized_title=f"execution work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/execution/{suffix}",
        byte_size=max(1, len(text.encode("utf-8"))),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Execution Edition {suffix}",
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
    return entry, document, section, chunk


async def _persist_plan(
    session: AsyncSession,
    *,
    principal_id: UUID,
    context: ContextManifest,
    proposal: PlanProposal,
) -> Plan:
    registry = CapabilityRegistry()
    policy = ActionPolicy(registry)
    decisions = [policy.evaluate(step, context) for step in proposal.steps]
    plan, _ = await PlanRepository(session).create(
        principal_id=principal_id,
        user_request="Execute a bounded test plan.",
        proposal=proposal,
        context=context,
        decisions=decisions,
    )
    return plan


def _context(*capabilities: CapabilityName) -> ContextManifest:
    return ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=0,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[],
        available_capabilities=[capability.value for capability in capabilities],
        exclusion_reasons=[],
    )


def _research_step(entry_id: UUID, *, arguments: dict[str, object] | None = None) -> PlanStep:
    return PlanStep.model_validate(
        {
            "step_id": "research",
            "capability": CapabilityName.RESEARCH_SEARCH.value,
            "arguments": arguments
            or {
                "query": "maritime navigation",
                "library_entry_ids": [str(entry_id)],
                "limit": 10,
            },
            "rationale": "Search the principal-owned research corpus.",
        }
    )


def _coordinator(session: AsyncSession) -> ExecutionCoordinator:
    scope = _scope(session)
    research = ResearchSearchExecutor(ResearchService(session_scope_factory=scope))
    return ExecutionCoordinator(
        executor_registry=CapabilityExecutorRegistry((research,)),
        session_scope_factory=scope,
    )


async def test_executes_real_research_service_with_exact_citation_and_is_repeatable(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "research")
    entry, document, section, chunk = await _seed_research_book(
        session,
        principal=principal,
        suffix="navigation",
        text="Maritime navigation relied on documented routes and celestial observations.",
    )
    plan = await _persist_plan(
        session,
        principal_id=principal.id,
        context=_context(CapabilityName.RESEARCH_SEARCH),
        proposal=PlanProposal(summary="Search owned books.", steps=[_research_step(entry.id)]),
    )
    coordinator = _coordinator(session)

    first = await coordinator.execute(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id="research",
    )
    second = await coordinator.execute(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id="research",
    )

    assert first.capability is CapabilityName.RESEARCH_SEARCH
    assert first.output == second.output
    assert isinstance(first.output, dict)
    passages = first.output["passages"]
    assert isinstance(passages, list)
    assert len(passages) == 1
    passage = passages[0]
    assert isinstance(passage, dict)
    assert passage["document_id"] == str(document.id)
    assert passage["section_id"] == str(section.id)
    assert passage["chunk_id"] == str(chunk.id)
    assert passage["locator"] == {"page": 1, "section": "navigation"}


async def test_cannot_execute_another_principals_plan(session: AsyncSession) -> None:
    owner = await _principal(session, "owner")
    intruder = await _principal(session, "intruder")
    entry, _, _, _ = await _seed_research_book(
        session,
        principal=owner,
        suffix="private",
        text="Private maritime navigation evidence.",
    )
    plan = await _persist_plan(
        session,
        principal_id=owner.id,
        context=_context(CapabilityName.RESEARCH_SEARCH),
        proposal=PlanProposal(summary="Private plan.", steps=[_research_step(entry.id)]),
    )

    with pytest.raises(ActionExecutionNotFound):
        await _coordinator(session).execute(
            principal_id=intruder.id,
            plan_id=plan.id,
            step_id="research",
        )


async def test_ai_disabled_after_planning_blocks_execution(session: AsyncSession) -> None:
    principal = await _principal(session, "disabled")
    entry, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="disabled",
        text="Maritime navigation evidence that must not execute while AI is disabled.",
    )
    plan = await _persist_plan(
        session,
        principal_id=principal.id,
        context=_context(CapabilityName.RESEARCH_SEARCH),
        proposal=PlanProposal(summary="Plan before disable.", steps=[_research_step(entry.id)]),
    )
    user_model = await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    user_model.ai_enabled = False
    await session.flush()

    with pytest.raises(AIExecutionDisabled):
        await _coordinator(session).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="research",
        )


async def test_require_approval_action_never_reaches_executor(session: AsyncSession) -> None:
    principal = await _principal(session, "approval")
    context = _context(CapabilityName.ACQUISITION_REQUEST)
    proposal = PlanProposal(
        summary="Request an acquisition.",
        steps=[
            PlanStep(
                step_id="acquire",
                capability=CapabilityName.ACQUISITION_REQUEST,
                arguments={"asset_id": str(UUID(int=1))},
                rationale="Consequential action must remain approval-bound.",
            )
        ],
    )
    plan = await _persist_plan(
        session,
        principal_id=principal.id,
        context=context,
        proposal=proposal,
    )

    with pytest.raises(ActionApprovalRequired):
        await _coordinator(session).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="acquire",
        )


async def test_malformed_capability_arguments_fail_before_domain_execution(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "bad-args")
    plan = await _persist_plan(
        session,
        principal_id=principal.id,
        context=_context(CapabilityName.RESEARCH_SEARCH),
        proposal=PlanProposal(
            summary="Malformed arguments.",
            steps=[
                _research_step(
                    UUID(int=1),
                    arguments={"query": "", "library_entry_ids": [], "limit": 1},
                )
            ],
        ),
    )

    with pytest.raises(CapabilityArgumentsInvalid):
        await _coordinator(session).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="research",
        )


async def test_allowed_capability_without_executor_fails_closed(session: AsyncSession) -> None:
    principal = await _principal(session, "missing-executor")
    context = _context(CapabilityName.READER_OPEN)
    proposal = PlanProposal(
        summary="Open an owned document.",
        steps=[
            PlanStep(
                step_id="open",
                capability=CapabilityName.READER_OPEN,
                arguments={"document_id": str(UUID(int=2))},
                rationale="Read-only but no executor ships in this slice.",
            )
        ],
    )
    plan = await _persist_plan(
        session,
        principal_id=principal.id,
        context=context,
        proposal=proposal,
    )

    with pytest.raises(CapabilityExecutorUnavailable):
        await _coordinator(session).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="open",
        )


async def test_stale_context_and_missing_decision_fail_closed(session: AsyncSession) -> None:
    principal = await _principal(session, "integrity")
    entry, _, _, _ = await _seed_research_book(
        session,
        principal=principal,
        suffix="integrity",
        text="Maritime navigation evidence.",
    )
    plan = await _persist_plan(
        session,
        principal_id=principal.id,
        context=_context(CapabilityName.RESEARCH_SEARCH),
        proposal=PlanProposal(summary="Integrity plan.", steps=[_research_step(entry.id)]),
    )
    plan.context_manifest = _context().model_dump(mode="json")
    await session.flush()

    with pytest.raises(ActionExecutionDenied):
        await _coordinator(session).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="research",
        )

    plan.context_manifest = _context(CapabilityName.RESEARCH_SEARCH).model_dump(mode="json")
    await session.execute(
        delete(ActionDecision).where(
            ActionDecision.plan_id == plan.id,
            ActionDecision.step_id == "research",
        )
    )
    await session.flush()

    with pytest.raises(PlanIntegrityError):
        await _coordinator(session).execute(
            principal_id=principal.id,
            plan_id=plan.id,
            step_id="research",
        )
