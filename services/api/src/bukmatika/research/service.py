from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.research import ResearchRepository, ResearchSelectionDenied
from bukmatika.research.domain import (
    ResearchPassageResponse,
    ResearchSearchRequest,
    ResearchSearchResponse,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ResearchService:
    """Grounded lexical retrieval over explicitly selected owned books."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def search(
        self,
        *,
        principal_id: UUID,
        request: ResearchSearchRequest,
    ) -> ResearchSearchResponse:
        async with self._session_scope() as database_session:
            repository = ResearchRepository(database_session)
            matches = await repository.search_owned_passages(
                principal_id=principal_id,
                library_entry_ids=request.library_entry_ids,
                query=request.query,
                limit=request.limit,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.RESEARCH_SEARCH_COMPLETED,
                principal_id=principal_id,
                entity_type="library_selection",
                context={
                    "query": request.query,
                    "selected_library_entry_ids": [
                        str(entry_id) for entry_id in request.library_entry_ids
                    ],
                    "result_count": len(matches),
                },
            )
            return ResearchSearchResponse(
                query=request.query,
                selected_library_entry_ids=request.library_entry_ids,
                passages=[
                    ResearchPassageResponse(
                        library_entry_id=match.context.library_entry_id,
                        work_id=match.context.work_id,
                        work_title=match.context.work_title,
                        edition_id=match.context.edition_id,
                        edition_title=match.context.edition_title,
                        asset_id=match.context.asset_id,
                        document_id=match.context.document_id,
                        chunk_id=match.chunk_id,
                        section_id=match.section_id,
                        section_ordinal=match.section_ordinal,
                        chunk_ordinal=match.chunk_ordinal,
                        heading=match.heading,
                        locator=match.locator,
                        char_start=match.char_start,
                        char_end=match.char_end,
                        text=match.text,
                        score=max(0.0, match.score),
                    )
                    for match in matches
                ],
            )


__all__ = ["ResearchSelectionDenied", "ResearchService"]
