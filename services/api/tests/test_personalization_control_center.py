from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
from bukmatika.persistence.models import InteractionEvent, Principal
from bukmatika.persistence.personalization_models import (
    ActionDecision,
    Goal,
    OutcomeEvent,
    Plan,
    PreferenceClaim,
    PreferenceClaimEvidence,
    UserModel,
)
from bukmatika.personalization.control import PersonalizationControlService
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PreferenceKey,
    PreferenceScopeType,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"control-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


async def _user_model(session: AsyncSession, principal_id):  # type: ignore[no-untyped-def]
    service = PersonalizationService(session_scope_factory=_scope(session))
    await service.profile(principal_id=principal_id)
    model = await session.scalar(select(UserModel).where(UserModel.principal_id == principal_id))
    assert model is not None
    return model


async def test_control_center_is_principal_scoped_and_redacts_raw_context(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    owner_model = await _user_model(session, owner.id)
    other_model = await _user_model(session, other.id)

    explicit_service = PersonalizationService(session_scope_factory=_scope(session))
    explicit = await explicit_service.set_explicit_preference(
        principal_id=owner.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.CITATION_STYLE,
            value={"style": "Chicago"},
            scope_type=PreferenceScopeType.RESEARCH,
            scope_value="history",
        ),
    )

    first_event = InteractionEvent(
        principal_id=owner.id,
        event_type="reader.opened",
        entity_type="document",
        entity_id=uuid4(),
        context={"private": "raw-evidence-secret"},
    )
    second_event = InteractionEvent(
        principal_id=owner.id,
        event_type="reader.opened",
        entity_type="document",
        entity_id=uuid4(),
        context={"private": "another-evidence-secret"},
    )
    session.add_all([first_event, second_event])
    await session.flush()

    inferred = PreferenceClaim(
        user_model_id=owner_model.id,
        principal_id=owner.id,
        key=PreferenceKey.FORMAT_PREFERRED.value,
        value={"format": "EPUB"},
        source="inferred",
        status="active",
        confidence=0.8,
        scope_type=PreferenceScopeType.GLOBAL.value,
        scope_value="",
        evidence_count=2,
        influence={"ranking": True, "presentation": True, "automation": False},
    )
    session.add(inferred)
    await session.flush()
    session.add_all(
        [
            PreferenceClaimEvidence(
                preference_claim_id=inferred.id,
                interaction_event_id=first_event.id,
            ),
            PreferenceClaimEvidence(
                preference_claim_id=inferred.id,
                interaction_event_id=second_event.id,
            ),
        ]
    )

    owner_goal = Goal(
        principal_id=owner.id,
        title="Compare Atlantic history sources",
        kind="research",
        status="active",
        scope={"private": "goal-scope-secret"},
        constraints={"private": "goal-constraint-secret"},
    )
    other_goal = Goal(
        principal_id=other.id,
        title="Other principal goal",
        kind="research",
        status="active",
    )
    session.add_all([owner_goal, other_goal])
    await session.flush()

    owner_plan = Plan(
        principal_id=owner.id,
        goal_id=owner_goal.id,
        status="proposed",
        user_request="Find evidence across my selected books",
        planner_version="structured-planner-v1",
        steps=[
            {
                "step_id": "research-1",
                "capability": "research.search",
                "arguments": {"query": "PRIVATE STEP ARGUMENT"},
                "rationale": "Search the selected books for relevant passages.",
            }
        ],
        context_manifest={"private": "raw-context-manifest-secret"},
    )
    other_plan = Plan(
        principal_id=other.id,
        goal_id=other_goal.id,
        status="proposed",
        user_request="Other principal request",
        planner_version="structured-planner-v1",
        steps=[
            {
                "step_id": "other-1",
                "capability": "research.search",
                "arguments": {"query": "other"},
                "rationale": "Other rationale",
            }
        ],
        context_manifest={"private": "other-context"},
    )
    session.add_all([owner_plan, other_plan])
    await session.flush()

    owner_decision = ActionDecision(
        principal_id=owner.id,
        plan_id=owner_plan.id,
        step_id="research-1",
        capability="research.search",
        decision="require_approval",
        reason="This action requires explicit approval under the current policy.",
        policy_version="action-policy-v1",
    )
    other_decision = ActionDecision(
        principal_id=other.id,
        plan_id=other_plan.id,
        step_id="other-1",
        capability="research.search",
        decision="allow",
        reason="Other principal decision",
        policy_version="action-policy-v1",
    )
    session.add_all([owner_decision, other_decision])
    await session.flush()

    owner_outcome = OutcomeEvent(
        principal_id=owner.id,
        plan_id=owner_plan.id,
        action_decision_id=owner_decision.id,
        outcome="accepted",
        context={"private": "raw-outcome-secret"},
    )
    other_outcome = OutcomeEvent(
        principal_id=other.id,
        plan_id=other_plan.id,
        action_decision_id=other_decision.id,
        outcome="helped",
        context={"private": "other-outcome"},
    )
    session.add_all([owner_outcome, other_outcome])
    await session.flush()

    other_claim = PreferenceClaim(
        user_model_id=other_model.id,
        principal_id=other.id,
        key=PreferenceKey.FORMAT_PREFERRED.value,
        value={"format": "PDF"},
        source="inferred",
        status="active",
        confidence=0.9,
        scope_type=PreferenceScopeType.GLOBAL.value,
        scope_value="",
        evidence_count=0,
        influence={"ranking": True, "presentation": True, "automation": False},
    )
    session.add(other_claim)
    await session.flush()

    control = PersonalizationControlService(session_scope_factory=_scope(session))
    snapshot = await control.snapshot(principal_id=owner.id)

    assert [claim.claim_id for claim in snapshot.explicit_preferences] == [explicit.claim_id]
    assert [claim.claim_id for claim in snapshot.inferred_preferences] == [inferred.id]
    provenance = snapshot.inferred_preferences[0].provenance
    assert provenance is not None
    assert provenance.event_count == 2
    assert provenance.distinct_entity_count == 2
    assert provenance.event_types == ["reader.opened"]

    assert [goal.goal_id for goal in snapshot.active_goals] == [owner_goal.id]
    assert len(snapshot.recent_activity) == 1
    activity = snapshot.recent_activity[0]
    assert activity.plan_id == owner_plan.id
    assert activity.decision_id == owner_decision.id
    assert activity.approval_required is True
    assert activity.rationale == "Search the selected books for relevant passages."
    assert activity.latest_outcome == "accepted"
    assert activity.model_provider is None
    assert activity.model_name is None

    assert [outcome.outcome_id for outcome in snapshot.recent_outcomes] == [owner_outcome.id]

    payload = snapshot.model_dump_json()
    assert "raw-evidence-secret" not in payload
    assert "another-evidence-secret" not in payload
    assert "PRIVATE STEP ARGUMENT" not in payload
    assert "raw-context-manifest-secret" not in payload
    assert "raw-outcome-secret" not in payload
    assert "Other principal" not in payload


async def test_activity_ledger_returns_only_persisted_principal_evidence(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "ledger")
    await _user_model(session, principal.id)

    plan = Plan(
        principal_id=principal.id,
        status="proposed",
        user_request="Search my books for primary-source references",
        planner_version="structured-planner-v1",
        steps=[
            {
                "step_id": "step-1",
                "capability": "research.search",
                "arguments": {"query": "primary source"},
                "rationale": "Search only the selected owned books.",
            }
        ],
        context_manifest={},
    )
    session.add(plan)
    await session.flush()
    decision = ActionDecision(
        principal_id=principal.id,
        plan_id=plan.id,
        step_id="step-1",
        capability="research.search",
        decision="allow",
        reason="Read-only research is allowed in the persisted selection.",
        policy_version="action-policy-v1",
    )
    session.add(decision)
    await session.flush()
    session.add(
        OutcomeEvent(
            principal_id=principal.id,
            plan_id=plan.id,
            action_decision_id=decision.id,
            outcome="helped",
            context={"raw": "not-for-ledger"},
        )
    )
    await session.flush()

    control = PersonalizationControlService(session_scope_factory=_scope(session))
    ledger = await control.activity(principal_id=principal.id, limit=10)

    assert len(ledger.items) == 1
    item = ledger.items[0]
    assert item.capability == "research.search"
    assert item.decision == "allow"
    assert item.approval_required is False
    assert item.latest_outcome == "helped"
    assert item.outcomes[0].outcome == "helped"
    assert item.model_provider is None
    assert item.model_name is None
    assert "not-for-ledger" not in ledger.model_dump_json()


def test_control_center_routes_are_mounted() -> None:
    paths = set(app.openapi()["paths"])
    assert "/v1/personalization/control-center" in paths
    assert "/v1/personalization/activity" in paths
