from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai import (
    ActionDecisionValue,
    ActionPolicy,
    AIDisabled,
    CapabilityName,
    CapabilityRegistry,
    ModelDataClassification,
    ModelProviderUnconfigured,
    ModelRequest,
    ModelTask,
    PersistedPlanResponse,
    PlanCapabilityUnavailable,
    PlanningService,
    PlanProposal,
    PlanStep,
    UnconfiguredModelGateway,
)
from bukmatika.ai.gateway import StructuredResponseT
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization_models import ActionDecision, Plan
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import (
    ContextManifest,
    ContextRequest,
    ContextTask,
    PersonalizationSettingsUpdate,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"ai-planning-{suffix}")
    session.add(principal)
    await session.flush()
    return principal


class RecordingGateway:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[ModelRequest] = []

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        self.calls.append(request)
        return response_type.model_validate(self.payload)


async def test_unconfigured_gateway_fails_closed_without_fake_output() -> None:
    gateway = UnconfiguredModelGateway()
    with pytest.raises(ModelProviderUnconfigured) as error:
        await gateway.generate_structured(
            ModelRequest(
                task=ModelTask.PLAN,
                payload={"user_request": "Find relevant books"},
                data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
                max_output_tokens=512,
                timeout_seconds=10,
            ),
            PlanProposal,
        )
    assert error.value.code == "MODEL_PROVIDER_UNCONFIGURED"


async def test_default_planning_service_leaves_no_plan_when_provider_unconfigured(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "unconfigured")
    scope = _scope(session)
    service = PlanningService(
        context_assembler=ContextAssembler(session_scope_factory=scope),
        session_scope_factory=scope,
    )

    with pytest.raises(ModelProviderUnconfigured) as error:
        await service.propose(
            principal_id=principal.id,
            user_request="Search my books for navigation evidence.",
            context_request=ContextRequest(task=ContextTask.RESEARCH),
        )

    assert error.value.code == "MODEL_PROVIDER_UNCONFIGURED"
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None


async def test_ai_disabled_short_circuits_before_gateway_or_plan_persistence(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "disabled")
    scope = _scope(session)
    await PersonalizationService(session_scope_factory=scope).update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=True,
            autonomy_level=0,
        ),
    )
    gateway = RecordingGateway(_research_plan_payload())
    service = PlanningService(
        gateway=gateway,
        context_assembler=ContextAssembler(session_scope_factory=scope),
        session_scope_factory=scope,
    )

    with pytest.raises(AIDisabled) as error:
        await service.propose(
            principal_id=principal.id,
            user_request="Find passages about navigation.",
            context_request=ContextRequest(task=ContextTask.RESEARCH),
        )

    assert error.value.code == "AI_DISABLED"
    assert gateway.calls == []
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None


def test_unknown_capability_is_rejected_by_structured_plan_schema() -> None:
    with pytest.raises(ValidationError):
        PlanProposal.model_validate(
            {
                "summary": "Unsafe proposal",
                "steps": [
                    {
                        "step_id": "run-shell",
                        "capability": "shell.exec",
                        "arguments": {},
                        "rationale": "Try to escape the registry.",
                    }
                ],
            }
        )


def test_duplicate_step_ids_and_argument_budget_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanProposal(
            summary="Duplicate IDs",
            steps=[
                _step("same", CapabilityName.RESEARCH_SEARCH),
                _step("same", CapabilityName.READER_OPEN),
            ],
        )

    with pytest.raises(ValidationError):
        PlanStep(
            step_id="oversized",
            capability=CapabilityName.RESEARCH_SEARCH,
            arguments={"query": "x" * 9_000},
            rationale="Arguments are intentionally too large.",
        )


async def test_registered_but_context_unavailable_capability_is_rejected_before_persistence(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "context-deny")
    scope = _scope(session)
    gateway = RecordingGateway(
        {
            "summary": "Try a discovery capability in research context.",
            "steps": [
                {
                    "step_id": "discover",
                    "capability": CapabilityName.DISCOVERY_SEARCH.value,
                    "arguments": {"query": "Atlantic contact"},
                    "rationale": "This capability is registered but unavailable here.",
                }
            ],
        }
    )
    service = PlanningService(
        gateway=gateway,
        context_assembler=ContextAssembler(session_scope_factory=scope),
        session_scope_factory=scope,
    )

    with pytest.raises(PlanCapabilityUnavailable) as error:
        await service.propose(
            principal_id=principal.id,
            user_request="Search my books for evidence.",
            context_request=ContextRequest(task=ContextTask.RESEARCH),
        )

    assert error.value.code == "PLAN_CAPABILITY_UNAVAILABLE"
    assert len(gateway.calls) == 1
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None


async def test_valid_plan_gets_one_deterministic_decision_per_step(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "persist")
    scope = _scope(session)
    gateway = RecordingGateway(_research_plan_payload())
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

    _assert_valid_result(result)
    assert len(gateway.calls) == 1
    assert gateway.calls[0].data_classification is ModelDataClassification.PRIVATE_USER_CONTEXT
    stored_plan = await session.get(Plan, result.plan_id)
    assert stored_plan is not None
    assert stored_plan.principal_id == principal.id
    assert stored_plan.planner_version == "structured-planner-v1"
    assert len(stored_plan.steps) == 2

    decisions = list(
        (
            await session.scalars(
                select(ActionDecision)
                .where(ActionDecision.plan_id == result.plan_id)
                .order_by(ActionDecision.step_id)
            )
        ).all()
    )
    assert len(decisions) == 2
    assert {decision.decision for decision in decisions} == {"allow"}
    assert {decision.policy_version for decision in decisions} == {"ai-action-policy-v1"}


def test_consequential_acquisition_can_never_be_model_authorized() -> None:
    registry = CapabilityRegistry()
    policy = ActionPolicy(registry)
    context = ContextManifest(
        task=ContextTask.DISCOVERY,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=1,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[],
        available_capabilities=[CapabilityName.ACQUISITION_REQUEST.value],
        exclusion_reasons=[],
    )
    decision = policy.evaluate(
        _step("acquire", CapabilityName.ACQUISITION_REQUEST),
        context,
    )
    assert decision.decision is ActionDecisionValue.REQUIRE_APPROVAL
    assert "domain policy" in decision.reason


def _research_plan_payload() -> dict[str, Any]:
    return {
        "summary": "Search the owned research corpus and open the strongest source.",
        "steps": [
            {
                "step_id": "search",
                "capability": CapabilityName.RESEARCH_SEARCH.value,
                "arguments": {"query": "navigation"},
                "rationale": "Find grounded passages in the selected owned books.",
            },
            {
                "step_id": "open",
                "capability": CapabilityName.READER_OPEN.value,
                "arguments": {"result_rank": 1},
                "rationale": "Open the strongest grounded passage for inspection.",
            },
        ],
    }


def _step(step_id: str, capability: CapabilityName) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        capability=capability,
        arguments={},
        rationale="Deterministic test step.",
    )


def _assert_valid_result(result: PersistedPlanResponse) -> None:
    assert result.status == "proposed"
    assert [step.step_id for step in result.steps] == ["search", "open"]
    assert [decision.step_id for decision in result.decisions] == ["search", "open"]
    assert {decision.decision for decision in result.decisions} == {"allow"}
