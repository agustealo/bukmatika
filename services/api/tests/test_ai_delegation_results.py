from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_delegation_runtime import (
    RUNTIME_ENTRY_ID,
    _plan,
    _principal,
    _running_delegation,
    _scope,
    _step,
)

from bukmatika.ai.delegation import DelegationControlService
from bukmatika.ai.delegation_domain import (
    DelegationApprovalDecision,
    DelegationApprovalRequest,
    DelegationAttemptCompletion,
    DelegationProposalRequest,
    DelegationStatus,
)
from bukmatika.ai.delegation_result_domain import DelegatedResearchSearchReceipt
from bukmatika.ai.delegation_results import recent_delegation_results
from bukmatika.ai.domain import CapabilityName
from bukmatika.persistence.delegation_models import AIDelegationAttempt
from bukmatika.persistence.delegation_results import DelegationResultRepository
from bukmatika.persistence.models import LibraryEntry, Work

EXPECTED_USER_REQUEST = "Run one bounded delegated research step."


async def _owned_source(
    session: AsyncSession,
    *,
    principal_id: UUID,
    suffix: str,
) -> LibraryEntry:
    work = Work(
        canonical_title=f"Delegation result source {suffix}",
        normalized_title=f"delegation result source {suffix}",
    )
    session.add(work)
    await session.flush()
    entry = LibraryEntry(
        principal_id=principal_id,
        work_id=work.id,
        status="saved",
    )
    session.add(entry)
    await session.flush()
    return entry


async def test_recent_outcomes_preserve_successful_completed_receipt(
    session: AsyncSession,
) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="result-projection-success",
    )
    source = await _owned_source(
        session,
        principal_id=principal.id,
        suffix="success",
    )
    permit = await control.authorize_next_step(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    attempt = await session.get(AIDelegationAttempt, permit.attempt_id)
    assert attempt is not None
    receipt = DelegatedResearchSearchReceipt(
        query="projection success",
        selected_library_entry_ids=[source.id],
        passages=[],
    )
    await DelegationResultRepository(session).store_for_attempt(
        attempt=attempt,
        capability=CapabilityName.RESEARCH_SEARCH.value,
        receipt=receipt.model_dump(mode="json"),
    )
    await control.complete_attempt(
        principal_id=principal.id,
        delegation_id=delegation_id,
        attempt_id=permit.attempt_id,
        completion=DelegationAttemptCompletion(succeeded=True),
    )

    outcomes = await recent_delegation_results(session, principal_id=principal.id)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.delegation_id == delegation_id
    assert outcome.attempt_id == permit.attempt_id
    assert outcome.status is DelegationStatus.COMPLETED
    assert outcome.available is True
    assert outcome.query == "projection success"
    assert outcome.user_request is None
    assert outcome.completed_at == outcome.outcome_at
    assert outcome.failure_code is None
    assert outcome.attempt_error_code is None
    assert outcome.selected_library_entry_ids == [source.id]
    assert len(outcome.selected_sources) == 1
    assert outcome.selected_sources[0].library_entry_id == source.id
    assert outcome.selected_sources[0].work_title == "Delegation result source success"
    assert outcome.selected_sources[0].edition_title is None
    assert outcome.selected_sources[0].available is True


async def test_recent_outcomes_surface_terminal_failure_codes(session: AsyncSession) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="result-projection-failed",
    )
    permit = await control.authorize_next_step(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    failed = await control.complete_attempt(
        principal_id=principal.id,
        delegation_id=delegation_id,
        attempt_id=permit.attempt_id,
        completion=DelegationAttemptCompletion(
            succeeded=False,
            error_code="DELEGATED_SEARCH_FAILED",
        ),
    )
    assert failed.status is DelegationStatus.FAILED

    outcomes = await recent_delegation_results(session, principal_id=principal.id)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.delegation_id == delegation_id
    assert outcome.attempt_id == permit.attempt_id
    assert outcome.status is DelegationStatus.FAILED
    assert outcome.available is False
    assert outcome.failure_code == "DELEGATED_SEARCH_FAILED"
    assert outcome.attempt_error_code == "DELEGATED_SEARCH_FAILED"
    assert outcome.user_request == EXPECTED_USER_REQUEST
    assert outcome.completed_at == outcome.outcome_at
    assert outcome.capability == CapabilityName.RESEARCH_SEARCH.value
    assert outcome.query == "delegated runtime evidence"
    assert outcome.selected_library_entry_ids == [RUNTIME_ENTRY_ID]
    assert len(outcome.selected_sources) == 1
    assert outcome.selected_sources[0].library_entry_id == RUNTIME_ENTRY_ID
    assert outcome.selected_sources[0].work_title is None
    assert outcome.selected_sources[0].available is False


async def test_recent_outcomes_keep_rejected_without_attempt(session: AsyncSession) -> None:
    principal = await _principal(session, "result-projection-rejected")
    plan = await _plan(
        session,
        principal_id=principal.id,
        step=_step(CapabilityName.RESEARCH_SEARCH),
    )
    control = DelegationControlService(session_scope_factory=_scope(session))
    proposed = await control.propose(
        principal_id=principal.id,
        plan_id=plan.id,
        request=DelegationProposalRequest(
            step_ids=["research"],
            max_runtime_seconds=300,
            max_retries_per_step=0,
            max_total_attempts=1,
        ),
    )
    rejected = await control.decide(
        principal_id=principal.id,
        delegation_id=proposed.delegation_id,
        request=DelegationApprovalRequest(decision=DelegationApprovalDecision.REJECTED),
    )
    assert rejected.status is DelegationStatus.REJECTED

    outcomes = await recent_delegation_results(session, principal_id=principal.id)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.delegation_id == proposed.delegation_id
    assert outcome.status is DelegationStatus.REJECTED
    assert outcome.attempt_id is None
    assert outcome.step_id is None
    assert outcome.user_request == EXPECTED_USER_REQUEST
    assert outcome.available is False
    assert outcome.completed_at is None
    assert outcome.query == "delegated runtime evidence"
    assert outcome.selected_library_entry_ids == [RUNTIME_ENTRY_ID]
    assert len(outcome.selected_sources) == 1
    assert outcome.selected_sources[0].available is False


async def test_recent_outcomes_are_principal_scoped(session: AsyncSession) -> None:
    principal, _, control, delegation_id = await _running_delegation(
        session,
        suffix="result-projection-owner",
    )
    permit = await control.authorize_next_step(
        principal_id=principal.id,
        delegation_id=delegation_id,
    )
    await control.complete_attempt(
        principal_id=principal.id,
        delegation_id=delegation_id,
        attempt_id=permit.attempt_id,
        completion=DelegationAttemptCompletion(
            succeeded=False,
            error_code="OWNER_ONLY_FAILURE",
        ),
    )
    other = await _principal(session, f"result-projection-other-{uuid4()}")

    assert await recent_delegation_results(session, principal_id=other.id) == []
