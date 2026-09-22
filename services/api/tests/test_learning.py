from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.learning import OutcomeReferenceDenied
from bukmatika.persistence.models import Asset, Edition, Principal, StoredObject, Work
from bukmatika.persistence.personalization_models import (
    ActionDecision,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
    PreferenceClaimEvidence,
)
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PersonalizationSettingsUpdate,
    PreferenceKey,
)
from bukmatika.personalization.learning import LearningService, OutcomeCreate, OutcomeValue
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"learning-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def _reader_open_event(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    format_name: str,
):  # type: ignore[no-untyped-def]
    work = Work(
        canonical_title=f"Learning Work {suffix}",
        normalized_title=f"learning work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/learning/{suffix}",
        byte_size=128,
        media_type="application/octet-stream",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Learning Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format=format_name,
        media_type="application/octet-stream",
        remote_url=f"https://example.org/{suffix}",
        stored_object_id=stored.id,
        byte_size=128,
    )
    session.add(asset)
    await session.flush()
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format=format_name,
        parser_name="test",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()
    event = await InteractionEventRepository(session).record(
        SemanticEventType.READER_OPENED,
        principal_id=principal.id,
        entity_type="document",
        entity_id=document.id,
        context={"library_entry_id": str(UUID(int=1))},
    )
    return document, event


async def _format_claim(session: AsyncSession, principal_id: UUID) -> PreferenceClaim | None:
    return await session.scalar(
        select(PreferenceClaim).where(
            PreferenceClaim.principal_id == principal_id,
            PreferenceClaim.key == PreferenceKey.FORMAT_PREFERRED.value,
            PreferenceClaim.status == "active",
        )
    )


async def _seed_plan_and_decision(
    session: AsyncSession,
    *,
    principal: Principal,
) -> tuple[Plan, ActionDecision]:
    plan = Plan(
        principal_id=principal.id,
        status="proposed",
        user_request="Test outcome ownership.",
        planner_version="test",
        steps=[],
        context_manifest={},
    )
    session.add(plan)
    await session.flush()
    decision = ActionDecision(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id="read",
        capability="research.search",
        decision="allow",
        reason="test",
        policy_version="test",
    )
    session.add(decision)
    await session.flush()
    return plan, decision


async def test_outcome_references_are_principal_scoped_and_action_derives_plan(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "outcome-owner")
    other = await _principal(session, "outcome-other")
    plan, decision = await _seed_plan_and_decision(session, principal=owner)
    service = LearningService(session_scope_factory=_scope(session))

    with pytest.raises(OutcomeReferenceDenied):
        await service.record_outcome(
            principal_id=other.id,
            request=OutcomeCreate(
                outcome=OutcomeValue.HELPED,
                plan_id=plan.id,
            ),
        )
    with pytest.raises(OutcomeReferenceDenied):
        await service.record_outcome(
            principal_id=other.id,
            request=OutcomeCreate(
                outcome=OutcomeValue.HELPED,
                action_decision_id=decision.id,
            ),
        )

    event_id = await service.record_outcome(
        principal_id=owner.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.HELPED,
            action_decision_id=decision.id,
        ),
    )
    event = await session.get(OutcomeEvent, event_id)
    assert event is not None
    assert event.principal_id == owner.id
    assert event.plan_id == plan.id
    assert event.action_decision_id == decision.id


async def test_weak_or_repeated_same_document_behavior_does_not_create_claim(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "weak")
    service = LearningService(session_scope_factory=_scope(session))
    document, _ = await _reader_open_event(
        session,
        principal=principal,
        suffix="weak-one",
        format_name="EPUB",
    )
    for _ in range(6):
        await InteractionEventRepository(session).record(
            SemanticEventType.READER_OPENED,
            principal_id=principal.id,
            entity_type="document",
            entity_id=document.id,
        )
    await _reader_open_event(
        session,
        principal=principal,
        suffix="weak-two",
        format_name="EPUB",
    )
    await _reader_open_event(
        session,
        principal=principal,
        suffix="weak-three",
        format_name="EPUB",
    )

    assert await service.refresh_format_preference(principal_id=principal.id) is None
    assert await _format_claim(session, principal.id) is None


async def test_repeated_distinct_format_evidence_creates_one_inferred_claim_with_provenance(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "infer")
    service = LearningService(session_scope_factory=_scope(session))
    evidence_ids: set[UUID] = set()
    for index in range(4):
        _, event = await _reader_open_event(
            session,
            principal=principal,
            suffix=f"infer-pdf-{index}",
            format_name="PDF",
        )
        evidence_ids.add(event.id)

    claim_id = await service.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None
    claim = await session.get(PreferenceClaim, claim_id)
    assert claim is not None
    assert claim.source == "inferred"
    assert claim.value == {"format": "PDF"}
    assert claim.scope_type == "global"
    assert claim.confidence > 0.7
    assert claim.evidence_count == 4
    assert claim.decay_half_life_days is not None
    assert claim.influence == {"ranking": True, "presentation": True, "automation": False}

    linked = set(
        (
            await session.scalars(
                select(PreferenceClaimEvidence.interaction_event_id).where(
                    PreferenceClaimEvidence.preference_claim_id == claim.id
                )
            )
        ).all()
    )
    assert linked == evidence_ids


async def test_reinforcement_updates_same_claim_without_duplicate_evidence(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "reinforce")
    service = LearningService(session_scope_factory=_scope(session))
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"reinforce-pdf-{index}",
            format_name="PDF",
        )
    claim_id = await service.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None
    first = await session.get(PreferenceClaim, claim_id)
    assert first is not None
    first_confidence = first.confidence

    await _reader_open_event(
        session,
        principal=principal,
        suffix="reinforce-pdf-4",
        format_name="PDF",
    )
    reinforced_id = await service.refresh_format_preference(principal_id=principal.id)
    assert reinforced_id == claim_id
    reinforced = await session.get(PreferenceClaim, claim_id)
    assert reinforced is not None
    assert reinforced.evidence_count == 5
    assert reinforced.confidence > first_confidence

    again_id = await service.refresh_format_preference(principal_id=principal.id)
    assert again_id == claim_id
    again = await session.get(PreferenceClaim, claim_id)
    assert again is not None
    assert again.evidence_count == 5


async def test_strong_contradictory_evidence_replaces_only_inferred_claim(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "contradiction")
    service = LearningService(session_scope_factory=_scope(session))
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"contradiction-pdf-{index}",
            format_name="PDF",
        )
    pdf_claim_id = await service.refresh_format_preference(principal_id=principal.id)
    assert pdf_claim_id is not None

    for index in range(6):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"contradiction-epub-{index}",
            format_name="EPUB",
        )
    epub_claim_id = await service.refresh_format_preference(principal_id=principal.id)
    assert epub_claim_id is not None
    assert epub_claim_id != pdf_claim_id

    old_claim = await session.get(PreferenceClaim, pdf_claim_id)
    new_claim = await session.get(PreferenceClaim, epub_claim_id)
    assert old_claim is not None
    assert new_claim is not None
    assert old_claim.status == "contradicted"
    assert old_claim.source == "inferred"
    assert new_claim.status == "active"
    assert new_claim.value == {"format": "EPUB"}


async def test_explicit_preference_blocks_inference_and_remains_untouched(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "explicit")
    scope = _scope(session)
    personalization = PersonalizationService(session_scope_factory=scope)
    explicit = await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "PDF"},
        ),
    )
    for index in range(6):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"explicit-epub-{index}",
            format_name="EPUB",
        )

    learned = await LearningService(session_scope_factory=scope).refresh_format_preference(
        principal_id=principal.id
    )
    assert learned is None
    claim = await session.get(PreferenceClaim, explicit.claim_id)
    assert claim is not None
    assert claim.source == "explicit"
    assert claim.status == "active"
    assert claim.confidence == 1.0
    assert claim.value == {"format": "PDF"}


async def test_corrected_outcome_contradicts_inferred_claim_but_not_explicit_claim(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "negative")
    scope = _scope(session)
    learning = LearningService(session_scope_factory=scope)
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"negative-pdf-{index}",
            format_name="PDF",
        )
    claim_id = await learning.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None

    await learning.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.CORRECTED,
            entity_type="preference_claim",
            entity_id=claim_id,
        ),
    )
    claim = await session.get(PreferenceClaim, claim_id)
    assert claim is not None
    assert claim.status == "contradicted"

    explicit = await PersonalizationService(session_scope_factory=scope).set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
        ),
    )
    await learning.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.CORRECTED,
            entity_type="preference_claim",
            entity_id=explicit.claim_id,
        ),
    )
    explicit_claim = await session.get(PreferenceClaim, explicit.claim_id)
    assert explicit_claim is not None
    assert explicit_claim.status == "active"
    assert explicit_claim.confidence == 1.0


async def test_learning_disabled_preserves_outcome_but_freezes_inferred_state(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "disabled")
    scope = _scope(session)
    learning = LearningService(session_scope_factory=scope)
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"disabled-pdf-{index}",
            format_name="PDF",
        )
    claim_id = await learning.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None
    claim = await session.get(PreferenceClaim, claim_id)
    assert claim is not None
    confidence = claim.confidence

    await PersonalizationService(session_scope_factory=scope).update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=True,
            learning_enabled=False,
            autonomy_level=0,
        ),
    )
    outcome_id = await learning.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.CORRECTED,
            entity_type="preference_claim",
            entity_id=claim_id,
        ),
    )

    outcome = await session.get(OutcomeEvent, outcome_id)
    frozen = await session.get(PreferenceClaim, claim_id)
    assert outcome is not None
    assert frozen is not None
    assert frozen.status == "active"
    assert frozen.confidence == confidence

    for index in range(6):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"disabled-epub-{index}",
            format_name="EPUB",
        )
    assert await learning.refresh_format_preference(principal_id=principal.id) is None


async def test_decay_expires_stale_inferred_claim_and_never_touches_explicit(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "decay")
    scope = _scope(session)
    learning = LearningService(session_scope_factory=scope)
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"decay-pdf-{index}",
            format_name="PDF",
        )
    inferred_id = await learning.refresh_format_preference(principal_id=principal.id)
    assert inferred_id is not None
    inferred = await session.get(PreferenceClaim, inferred_id)
    assert inferred is not None
    baseline = datetime.now(UTC)
    inferred.last_reinforced_at = baseline
    inferred.updated_at = baseline

    explicit = await PersonalizationService(session_scope_factory=scope).set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
        ),
    )
    explicit_claim = await session.get(PreferenceClaim, explicit.claim_id)
    assert explicit_claim is not None
    explicit_claim.updated_at = baseline
    await session.flush()

    changed = await learning.apply_decay(
        principal_id=principal.id,
        now=baseline + timedelta(days=200),
    )
    assert changed == 1
    stale = await session.get(PreferenceClaim, inferred_id)
    untouched = await session.get(PreferenceClaim, explicit.claim_id)
    assert stale is not None
    assert untouched is not None
    assert stale.status == "expired"
    assert stale.confidence < 0.35
    assert untouched.status == "active"
    assert untouched.confidence == 1.0
