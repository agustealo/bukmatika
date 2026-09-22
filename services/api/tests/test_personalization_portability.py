from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.personalization_models import (
    ActionDecision,
    Goal,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
    PreferenceClaimEvidence,
)
from bukmatika.persistence.reader_models import ReadingState
from bukmatika.personalization.control import PersonalizationControlService
from bukmatika.personalization.domain import ExplicitPreferenceRequest, PreferenceKey
from bukmatika.personalization.learning import LearningService
from bukmatika.personalization.portability import PersonalizationPortabilityService
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"portability-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def _seed_readable_corpus(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    document_count: int = 4,
) -> tuple[LibraryEntry, list[Document], ReadingState]:
    work = Work(
        canonical_title=f"Portability Work {suffix}",
        normalized_title=f"portability work {suffix}",
    )
    session.add(work)
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Portability Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add(entry)
    await session.flush()

    documents: list[Document] = []
    for index in range(document_count):
        token = uuid4().hex
        stored = StoredObject(
            sha256=token + token,
            storage_key=f"objects/portability/{suffix}/{token}",
            byte_size=128,
            media_type="application/epub+zip",
        )
        session.add(stored)
        await session.flush()
        asset = Asset(
            edition_id=edition.id,
            format="EPUB",
            media_type="application/epub+zip",
            remote_url=f"https://example.org/{suffix}/{index}.epub",
            stored_object_id=stored.id,
            byte_size=128,
        )
        session.add(asset)
        await session.flush()
        document = Document(
            asset_id=asset.id,
            stored_object_id=stored.id,
            source_sha256=stored.sha256,
            format="EPUB",
            parser_name="epub",
            parser_version="1",
            section_count=1,
            chunk_count=1,
        )
        session.add(document)
        await session.flush()
        section = DocumentSection(
            document_id=document.id,
            ordinal=0,
            heading=f"Section {index}",
            locator={"section": index},
            text=f"Research corpus text {index} about Atlantic navigation.",
        )
        session.add(section)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                section_id=section.id,
                ordinal=0,
                char_start=0,
                char_end=len(section.text),
                text=section.text,
            )
        )
        documents.append(document)
    await session.flush()

    state = ReadingState(
        library_entry_id=entry.id,
        document_id=documents[0].id,
        status="reading",
        progress_fraction=0.4,
        section_ordinal=0,
        char_offset=12,
        locator={"section": 0},
    )
    session.add(state)
    await session.flush()
    return entry, documents, state


async def _seed_personalization_records(
    session: AsyncSession,
    *,
    principal: Principal,
) -> tuple[Goal, Plan, ActionDecision, OutcomeEvent]:
    goal = Goal(
        principal_id=principal.id,
        title="Compare primary source accounts",
        kind="research",
        status="active",
        scope={"topic": "Atlantic contact"},
        constraints={"sources": "primary"},
    )
    session.add(goal)
    await session.flush()
    plan = Plan(
        principal_id=principal.id,
        goal_id=goal.id,
        status="proposed",
        user_request="Search my selected books for navigation evidence.",
        planner_version="structured-planner-v1",
        steps=[
            {
                "step_id": "research-1",
                "capability": "research.search",
                "arguments": {"query": "navigation"},
                "rationale": "Search the owned research corpus.",
            }
        ],
        context_manifest={"private_exportable": "owner-context"},
    )
    session.add(plan)
    await session.flush()
    decision = ActionDecision(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id="research-1",
        capability="research.search",
        decision="allow",
        reason="Read-only research is allowed.",
        policy_version="action-policy-v1",
    )
    session.add(decision)
    await session.flush()
    outcome = OutcomeEvent(
        principal_id=principal.id,
        plan_id=plan.id,
        action_decision_id=decision.id,
        outcome="helped",
        context={"private_exportable": "owner-outcome"},
    )
    session.add(outcome)
    await session.flush()
    return goal, plan, decision, outcome


async def test_export_is_finite_complete_and_principal_scoped(session: AsyncSession) -> None:
    owner = await _principal(session, "export-owner")
    other = await _principal(session, "export-other")
    scope = _scope(session)
    preferences = PersonalizationService(session_scope_factory=scope)
    owner_claim = await preferences.set_explicit_preference(
        principal_id=owner.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
        ),
    )
    other_claim = await preferences.set_explicit_preference(
        principal_id=other.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Private other style"},
        ),
    )
    event = await InteractionEventRepository(session).record(
        SemanticEventType.READER_OPENED,
        principal_id=owner.id,
        entity_type="document",
        entity_id=uuid4(),
        context={"must_not_export": "raw-interaction-secret"},
    )
    session.add(
        PreferenceClaimEvidence(
            preference_claim_id=owner_claim.claim_id,
            interaction_event_id=event.id,
        )
    )
    await session.flush()
    goal, plan, decision, outcome = await _seed_personalization_records(
        session,
        principal=owner,
    )
    await _seed_personalization_records(session, principal=other)

    exported = await PersonalizationPortabilityService(
        session_scope_factory=scope
    ).export(principal_id=owner.id)

    assert exported.schema_version == 1
    assert exported.user_model.user_model_id != other_claim.claim_id
    assert [claim.claim_id for claim in exported.preferences] == [owner_claim.claim_id]
    assert exported.preferences[0].evidence[0].interaction_event_id == event.id
    assert [item.goal_id for item in exported.goals] == [goal.id]
    assert [item.plan_id for item in exported.plans] == [plan.id]
    assert [item.decision_id for item in exported.action_decisions] == [decision.id]
    assert [item.outcome_id for item in exported.outcomes] == [outcome.id]
    payload = exported.model_dump_json()
    assert "owner-context" in payload
    assert "owner-outcome" in payload
    assert "raw-interaction-secret" not in payload
    assert "Private other style" not in payload
    assert str(other_claim.claim_id) not in payload


async def test_reset_is_atomic_preserves_product_state_and_blocks_old_learning_evidence(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "reset-owner")
    other = await _principal(session, "reset-other")
    scope = _scope(session)
    preferences = PersonalizationService(session_scope_factory=scope)
    original_model = await preferences.profile(principal_id=principal.id)
    other_claim = await preferences.set_explicit_preference(
        principal_id=other.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Other principal survives"},
        ),
    )
    entry, documents, reading_state = await _seed_readable_corpus(
        session,
        principal=principal,
        suffix="reset",
    )
    old_event_ids: list[UUID] = []
    for document in documents:
        event = await InteractionEventRepository(session).record(
            SemanticEventType.READER_OPENED,
            principal_id=principal.id,
            entity_type="document",
            entity_id=document.id,
            context={"library_entry_id": str(entry.id)},
        )
        old_event_ids.append(event.id)

    learning = LearningService(session_scope_factory=scope)
    inferred_id = await learning.refresh_format_preference(principal_id=principal.id)
    assert inferred_id is not None
    await preferences.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
        ),
    )
    await _seed_personalization_records(session, principal=principal)

    reset = await PersonalizationPortabilityService(session_scope_factory=scope).reset(
        principal_id=principal.id
    )

    assert reset.user_model_id != original_model.user_model_id
    assert reset.ai_enabled is True
    assert reset.learning_enabled is True
    assert reset.autonomy_level == 0
    assert (
        await session.scalar(
            select(PreferenceClaim).where(PreferenceClaim.principal_id == principal.id)
        )
        is None
    )
    assert await session.scalar(select(Goal).where(Goal.principal_id == principal.id)) is None
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None
    assert (
        await session.scalar(
            select(ActionDecision).where(ActionDecision.principal_id == principal.id)
        )
        is None
    )
    assert (
        await session.scalar(
            select(OutcomeEvent).where(OutcomeEvent.principal_id == principal.id)
        )
        is None
    )
    assert (
        await session.scalar(
            select(PreferenceClaimEvidence).join(
                PreferenceClaim,
                PreferenceClaim.id == PreferenceClaimEvidence.preference_claim_id,
            ).where(PreferenceClaim.principal_id == principal.id)
        )
        is None
    )

    assert await session.get(LibraryEntry, entry.id) is not None
    assert await session.get(ReadingState, reading_state.id) is not None
    for document in documents:
        assert await session.get(Document, document.id) is not None
        assert (
            await session.scalar(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
            )
            is not None
        )
    for event_id in old_event_ids:
        assert await session.get(InteractionEvent, event_id) is not None
    reset_event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == SemanticEventType.PERSONALIZATION_RESET.value,
        )
    )
    assert reset_event is not None
    assert reset_event.id not in old_event_ids

    surviving_other = await session.get(PreferenceClaim, other_claim.claim_id)
    assert surviving_other is not None
    assert surviving_other.status == "active"

    assert await learning.refresh_format_preference(principal_id=principal.id) is None
    assert (
        await session.scalar(
            select(PreferenceClaim).where(
                PreferenceClaim.principal_id == principal.id,
                PreferenceClaim.source == "inferred",
                PreferenceClaim.status == "active",
            )
        )
        is None
    )

    snapshot = await PersonalizationControlService(session_scope_factory=scope).snapshot(
        principal_id=principal.id
    )
    assert snapshot.user_model_id == reset.user_model_id
    assert snapshot.explicit_preferences == []
    assert snapshot.inferred_preferences == []
    assert snapshot.active_goals == []
    assert snapshot.recent_activity == []
    assert snapshot.recent_outcomes == []


async def test_export_after_reset_contains_only_fresh_default_state_and_reset_marker(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "post-reset-export")
    scope = _scope(session)
    preferences = PersonalizationService(session_scope_factory=scope)
    await preferences.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "MLA"},
        ),
    )
    service = PersonalizationPortabilityService(session_scope_factory=scope)
    reset = await service.reset(principal_id=principal.id)
    exported = await service.export(principal_id=principal.id)

    assert exported.user_model.user_model_id == reset.user_model_id
    assert exported.latest_reset_at == reset.reset_at
    assert exported.preferences == []
    assert exported.goals == []
    assert exported.plans == []
    assert exported.action_decisions == []
    assert exported.outcomes == []


def test_portability_routes_are_mounted() -> None:
    paths = set(app.openapi()["paths"])
    assert "/v1/personalization/export" in paths
    assert "/v1/personalization/reset" in paths
