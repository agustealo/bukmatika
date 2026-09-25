from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.capability_contracts import (
    CapabilityArgumentContractRegistry,
    CapabilityArgumentsInvalid,
    CapabilityContractUnavailable,
)
from bukmatika.ai.delegation_domain import DelegationStatus
from bukmatika.ai.delegation_result_domain import (
    DelegatedResearchPassageReceipt,
    DelegatedResearchSearchReceipt,
    DelegationRecentResult,
    DelegationResultBudget,
    DelegationResultSource,
)
from bukmatika.ai.domain import CapabilityName, PlanStep
from bukmatika.ai.execution import CapabilityExecutionResponse
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegation_models import AIDelegation, AIDelegationAttempt
from bukmatika.persistence.delegation_results import (
    DelegationResultRepository,
    StoredTerminalDelegation,
)
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Work
from bukmatika.personalization.domain import ContextManifest
from bukmatika.research.domain import (
    ResearchPassageResponse,
    ResearchSearchRequest,
    ResearchSearchResponse,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
RECENT_DELEGATION_RESULT_LIMIT = 8


class DelegationResultProjectionService:
    """Project bounded recent delegation outcomes from canonical durable truth."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def recent(
        self,
        *,
        principal_id: UUID,
        limit: int = RECENT_DELEGATION_RESULT_LIMIT,
    ) -> list[DelegationRecentResult]:
        async with self._session_scope() as database_session:
            return await recent_delegation_results(
                database_session,
                principal_id=principal_id,
                limit=limit,
            )


async def record_delegated_execution_result(
    database_session: AsyncSession,
    *,
    attempt: AIDelegationAttempt,
    execution: CapabilityExecutionResponse,
) -> None:
    if execution.capability is not CapabilityName.RESEARCH_SEARCH:
        raise RuntimeError("Delegated result capability is not supported")
    response = ResearchSearchResponse.model_validate(execution.output)
    receipt = DelegatedResearchSearchReceipt(
        query=response.query,
        selected_library_entry_ids=response.selected_library_entry_ids,
        passages=[
            DelegatedResearchPassageReceipt(
                library_entry_id=passage.library_entry_id,
                document_id=passage.document_id,
                chunk_id=passage.chunk_id,
                section_id=passage.section_id,
                section_ordinal=passage.section_ordinal,
                chunk_ordinal=passage.chunk_ordinal,
                char_start=passage.char_start,
                char_end=passage.char_end,
                score=passage.score,
            )
            for passage in response.passages
        ],
    )
    await DelegationResultRepository(database_session).store_for_attempt(
        attempt=attempt,
        capability=execution.capability.value,
        receipt=receipt.model_dump(mode="json"),
    )


async def recent_delegation_results(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    limit: int = RECENT_DELEGATION_RESULT_LIMIT,
) -> list[DelegationRecentResult]:
    if limit < 1:
        return []
    bounded_limit = min(limit, RECENT_DELEGATION_RESULT_LIMIT)
    repository = DelegationResultRepository(database_session)
    stored = await repository.recent_completed(
        principal_id=principal_id,
        limit=bounded_limit,
    )
    terminal = await repository.recent_terminal(
        principal_id=principal_id,
        limit=bounded_limit,
    )

    results: list[DelegationRecentResult] = []
    for stored_result in stored:
        finished_at = stored_result.attempt.finished_at
        if finished_at is None:
            continue
        budget = _budget_projection(stored_result.delegation)
        try:
            receipt = DelegatedResearchSearchReceipt.model_validate(stored_result.result.receipt)
        except ValidationError:
            results.append(
                DelegationRecentResult(
                    delegation_id=stored_result.attempt.delegation_id,
                    attempt_id=stored_result.attempt.id,
                    step_id=stored_result.attempt.step_id,
                    capability=stored_result.result.capability,
                    status=DelegationStatus.COMPLETED,
                    outcome_at=finished_at,
                    completed_at=finished_at,
                    user_request=stored_result.plan.user_request,
                    budget=budget,
                    available=False,
                    unavailable_reason="Delegated result receipt is no longer valid.",
                )
            )
            continue

        selected_sources = await _hydrate_selected_sources(
            database_session,
            principal_id=principal_id,
            library_entry_ids=receipt.selected_library_entry_ids,
        )
        try:
            passages = await _hydrate_passages(
                database_session,
                principal_id=principal_id,
                receipt=receipt,
            )
        except (LookupError, ValueError):
            results.append(
                DelegationRecentResult(
                    delegation_id=stored_result.attempt.delegation_id,
                    attempt_id=stored_result.attempt.id,
                    step_id=stored_result.attempt.step_id,
                    capability=stored_result.result.capability,
                    status=DelegationStatus.COMPLETED,
                    outcome_at=finished_at,
                    completed_at=finished_at,
                    user_request=stored_result.plan.user_request,
                    budget=budget,
                    available=False,
                    unavailable_reason=(
                        "Canonical delegated result sources are no longer available."
                    ),
                    query=receipt.query,
                    selected_library_entry_ids=receipt.selected_library_entry_ids,
                    selected_sources=selected_sources,
                )
            )
            continue
        results.append(
            DelegationRecentResult(
                delegation_id=stored_result.attempt.delegation_id,
                attempt_id=stored_result.attempt.id,
                step_id=stored_result.attempt.step_id,
                capability=stored_result.result.capability,
                status=DelegationStatus.COMPLETED,
                outcome_at=finished_at,
                completed_at=finished_at,
                user_request=stored_result.plan.user_request,
                budget=budget,
                available=True,
                query=receipt.query,
                selected_library_entry_ids=receipt.selected_library_entry_ids,
                selected_sources=selected_sources,
                passages=passages,
            )
        )

    for terminal_result in terminal:
        delegation = terminal_result.delegation
        attempt = terminal_result.attempt
        query, selected_ids = _terminal_research_scope(terminal_result)
        selected_sources = await _hydrate_selected_sources(
            database_session,
            principal_id=principal_id,
            library_entry_ids=selected_ids,
        )
        results.append(
            DelegationRecentResult(
                delegation_id=delegation.id,
                attempt_id=attempt.id if attempt is not None else None,
                step_id=attempt.step_id if attempt is not None else None,
                capability=(CapabilityName.RESEARCH_SEARCH.value if selected_ids else None),
                status=DelegationStatus(delegation.status),
                outcome_at=_terminal_outcome_at(delegation),
                completed_at=delegation.completed_at,
                failure_code=delegation.failure_code,
                attempt_error_code=attempt.error_code if attempt is not None else None,
                user_request=terminal_result.plan.user_request,
                budget=_budget_projection(delegation),
                available=False,
                query=query,
                selected_library_entry_ids=selected_ids,
                selected_sources=selected_sources,
            )
        )

    results.sort(key=lambda result: result.outcome_at, reverse=True)
    return results[:bounded_limit]


def _budget_projection(delegation: AIDelegation) -> DelegationResultBudget:
    return DelegationResultBudget(
        selected_step_count=len(delegation.selected_step_ids),
        attempts_used=delegation.attempts_used,
        max_total_attempts=delegation.max_total_attempts,
        max_retries_per_step=delegation.max_retries_per_step,
        max_runtime_seconds=delegation.max_runtime_seconds,
        started_at=delegation.started_at,
    )


def _terminal_outcome_at(delegation: AIDelegation) -> datetime:
    return delegation.completed_at or delegation.stopped_at or delegation.updated_at


def _terminal_research_scope(
    terminal_result: StoredTerminalDelegation,
) -> tuple[str | None, list[UUID]]:
    try:
        context = ContextManifest.model_validate(terminal_result.plan.context_manifest)
        steps = [PlanStep.model_validate(raw_step) for raw_step in terminal_result.plan.steps]
    except ValidationError:
        return None, []

    selected_step_ids = set(terminal_result.delegation.selected_step_ids)
    registry = CapabilityArgumentContractRegistry()
    requests: list[ResearchSearchRequest] = []
    for step in steps:
        if (
            step.step_id not in selected_step_ids
            or step.capability is not CapabilityName.RESEARCH_SEARCH
        ):
            continue
        try:
            validated = registry.validate(
                capability=step.capability,
                arguments=step.arguments,
                context=context,
            )
        except (CapabilityArgumentsInvalid, CapabilityContractUnavailable):
            return None, []
        if not isinstance(validated, ResearchSearchRequest):
            return None, []
        requests.append(validated)

    if not requests:
        return None, []
    library_entry_ids = list(
        dict.fromkeys(entry_id for request in requests for entry_id in request.library_entry_ids)
    )
    queries = list(dict.fromkeys(request.query for request in requests))
    return (queries[0] if len(queries) == 1 else None), library_entry_ids


async def _hydrate_selected_sources(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    library_entry_ids: list[UUID],
) -> list[DelegationResultSource]:
    requested = list(dict.fromkeys(library_entry_ids))
    if not requested:
        return []
    rows = (
        await database_session.execute(
            select(LibraryEntry.id, Work.canonical_title, Edition.title)
            .join(Work, Work.id == LibraryEntry.work_id)
            .outerjoin(
                Edition,
                and_(
                    LibraryEntry.edition_id == Edition.id,
                    Edition.work_id == LibraryEntry.work_id,
                ),
            )
            .where(
                LibraryEntry.principal_id == principal_id,
                LibraryEntry.id.in_(requested),
            )
        )
    ).all()
    current = {
        entry_id: DelegationResultSource(
            library_entry_id=entry_id,
            work_title=work_title,
            edition_title=edition_title,
            available=True,
        )
        for entry_id, work_title, edition_title in rows
    }
    return [
        current.get(
            entry_id,
            DelegationResultSource(library_entry_id=entry_id, available=False),
        )
        for entry_id in requested
    ]


async def _hydrate_passages(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    receipt: DelegatedResearchSearchReceipt,
) -> list[ResearchPassageResponse]:
    owned = set(
        (
            await database_session.scalars(
                select(LibraryEntry.id).where(
                    LibraryEntry.principal_id == principal_id,
                    LibraryEntry.id.in_(receipt.selected_library_entry_ids),
                )
            )
        ).all()
    )
    if owned != set(receipt.selected_library_entry_ids):
        raise LookupError("Delegated result selection is no longer owned")

    passages: list[ResearchPassageResponse] = []
    for reference in receipt.passages:
        if reference.library_entry_id not in owned:
            raise LookupError("Delegated result passage escaped the selected library scope")
        row = (
            await database_session.execute(
                select(
                    LibraryEntry,
                    Work,
                    Edition,
                    Asset,
                    Document,
                    DocumentSection,
                    DocumentChunk,
                )
                .join(Work, Work.id == LibraryEntry.work_id)
                .join(Edition, Edition.work_id == Work.id)
                .join(Asset, Asset.edition_id == Edition.id)
                .join(Document, Document.asset_id == Asset.id)
                .join(DocumentSection, DocumentSection.document_id == Document.id)
                .join(DocumentChunk, DocumentChunk.section_id == DocumentSection.id)
                .where(
                    LibraryEntry.id == reference.library_entry_id,
                    LibraryEntry.principal_id == principal_id,
                    or_(
                        LibraryEntry.edition_id.is_(None),
                        LibraryEntry.edition_id == Edition.id,
                    ),
                    Document.id == reference.document_id,
                    DocumentSection.id == reference.section_id,
                    DocumentChunk.id == reference.chunk_id,
                    DocumentChunk.document_id == Document.id,
                )
            )
        ).one_or_none()
        if row is None:
            raise LookupError("Delegated result passage is no longer canonical")
        entry, work, edition, asset, document, section, chunk = row
        if (
            section.ordinal != reference.section_ordinal
            or chunk.ordinal != reference.chunk_ordinal
            or chunk.char_start != reference.char_start
            or chunk.char_end != reference.char_end
        ):
            raise ValueError("Delegated result passage coordinates changed")
        passages.append(
            ResearchPassageResponse(
                library_entry_id=entry.id,
                work_id=work.id,
                work_title=work.canonical_title,
                edition_id=edition.id,
                edition_title=edition.title,
                asset_id=asset.id,
                document_id=document.id,
                chunk_id=chunk.id,
                section_id=section.id,
                section_ordinal=section.ordinal,
                chunk_ordinal=chunk.ordinal,
                heading=section.heading,
                locator=section.locator,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                text=chunk.text,
                score=reference.score,
            )
        )
    return passages


__all__ = [
    "RECENT_DELEGATION_RESULT_LIMIT",
    "DelegationResultProjectionService",
    "recent_delegation_results",
    "record_delegated_execution_result",
]
