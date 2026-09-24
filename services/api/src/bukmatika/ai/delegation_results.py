from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.delegation_result_domain import (
    DelegatedResearchPassageReceipt,
    DelegatedResearchSearchReceipt,
    DelegationRecentResult,
)
from bukmatika.ai.domain import CapabilityName
from bukmatika.ai.execution import CapabilityExecutionResponse
from bukmatika.persistence import session_scope
from bukmatika.persistence.delegation_models import AIDelegationAttempt
from bukmatika.persistence.delegation_results import DelegationResultRepository
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Work
from bukmatika.research.domain import ResearchPassageResponse, ResearchSearchResponse

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
RECENT_DELEGATION_RESULT_LIMIT = 8


class DelegationResultProjectionService:
    """Hydrate bounded delegated result receipts from current canonical research truth."""

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
    stored = await DelegationResultRepository(database_session).recent_completed(
        principal_id=principal_id,
        limit=limit,
    )
    results: list[DelegationRecentResult] = []
    for item in stored:
        finished_at = item.attempt.finished_at
        if finished_at is None:
            continue
        try:
            receipt = DelegatedResearchSearchReceipt.model_validate(item.result.receipt)
            passages = await _hydrate_passages(
                database_session,
                principal_id=principal_id,
                receipt=receipt,
            )
        except (ValidationError, LookupError, ValueError):
            results.append(
                DelegationRecentResult(
                    delegation_id=item.attempt.delegation_id,
                    attempt_id=item.attempt.id,
                    step_id=item.attempt.step_id,
                    capability=item.result.capability,
                    completed_at=finished_at,
                    available=False,
                    unavailable_reason=(
                        "Canonical delegated result sources are no longer available."
                    ),
                )
            )
            continue
        results.append(
            DelegationRecentResult(
                delegation_id=item.attempt.delegation_id,
                attempt_id=item.attempt.id,
                step_id=item.attempt.step_id,
                capability=item.result.capability,
                completed_at=finished_at,
                available=True,
                query=receipt.query,
                selected_library_entry_ids=receipt.selected_library_entry_ids,
                passages=passages,
            )
        )
    return results


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
