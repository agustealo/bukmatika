from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import DocumentSection
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.reader_models import Bookmark, ReadingState
from bukmatika.persistence.readers import (
    ReaderAccessDenied,
    ReaderBookmarkNotFound,
    ReaderPositionInvalid,
    ReaderRepository,
)
from bukmatika.reader.domain import (
    BookmarkCreate,
    BookmarkResponse,
    ReaderDocumentResponse,
    ReaderSection,
    ReadingProgressUpdate,
    ReadingStateResponse,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ReaderService:
    """Consumer reader authority over canonical documents and durable reading state."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def open_reader(
        self,
        *,
        library_entry_id: UUID,
        document_id: UUID,
        after_ordinal: int | None,
        limit: int,
    ) -> ReaderDocumentResponse:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(library_entry_id, document_id)
            sections = await repository.sections(
                document_id,
                after_ordinal=after_ordinal,
                limit=limit,
            )
            reading_state = await repository.state_for(library_entry_id, document_id)
            bookmarks = await repository.bookmarks_for(library_entry_id, document_id)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.READER_OPENED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "after_ordinal": after_ordinal,
                    "section_count": len(sections),
                },
            )
            next_after = _next_after_ordinal(
                sections=sections,
                document_section_count=access.document.section_count,
                page_limit=limit,
            )
            return ReaderDocumentResponse(
                library_entry_id=library_entry_id,
                document_id=document_id,
                asset_id=access.document.asset_id,
                format=access.document.format,
                parser_name=access.document.parser_name,
                parser_version=access.document.parser_version,
                section_count=access.document.section_count,
                chunk_count=access.document.chunk_count,
                reading_state=_state_response(reading_state),
                bookmarks=[_bookmark_response(bookmark) for bookmark in bookmarks],
                sections=[
                    ReaderSection(
                        section_id=section.id,
                        ordinal=section.ordinal,
                        heading=section.heading,
                        locator=section.locator,
                        text=section.text,
                    )
                    for section in sections
                ],
                next_after_ordinal=next_after,
            )

    async def save_progress(
        self,
        *,
        library_entry_id: UUID,
        document_id: UUID,
        update: ReadingProgressUpdate,
    ) -> ReadingStateResponse:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(library_entry_id, document_id)
            state = await repository.save_progress(
                access=access,
                section_id=update.section_id,
                char_offset=update.char_offset,
                progress_fraction=update.progress_fraction,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.READING_PROGRESS_UPDATED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "section_id": str(update.section_id),
                    "char_offset": update.char_offset,
                    "progress_fraction": update.progress_fraction,
                },
            )
            response = _state_response(state)
            if response is None:
                raise RuntimeError("Persisted reading state disappeared")
            return response

    async def add_bookmark(
        self,
        *,
        library_entry_id: UUID,
        document_id: UUID,
        create: BookmarkCreate,
    ) -> BookmarkResponse:
        label = create.label.strip() if create.label is not None else None
        if label == "":
            label = None
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(library_entry_id, document_id)
            bookmark = await repository.add_bookmark(
                access=access,
                section_id=create.section_id,
                char_offset=create.char_offset,
                label=label,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.BOOKMARK_ADDED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "bookmark_id": str(bookmark.id),
                    "section_id": str(bookmark.section_id),
                    "char_offset": bookmark.char_offset,
                },
            )
            return _bookmark_response(bookmark)

    async def remove_bookmark(
        self,
        *,
        library_entry_id: UUID,
        document_id: UUID,
        bookmark_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(library_entry_id, document_id)
            await repository.remove_bookmark(access=access, bookmark_id=bookmark_id)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.BOOKMARK_REMOVED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "bookmark_id": str(bookmark_id),
                },
            )


def _state_response(state: ReadingState | None) -> ReadingStateResponse | None:
    if state is None:
        return None
    return ReadingStateResponse(
        reading_state_id=state.id,
        library_entry_id=state.library_entry_id,
        document_id=state.document_id,
        status=state.status,
        progress_fraction=state.progress_fraction,
        section_id=state.section_id,
        char_offset=state.char_offset,
        locator=state.locator,
    )


def _bookmark_response(bookmark: Bookmark) -> BookmarkResponse:
    return BookmarkResponse(
        bookmark_id=bookmark.id,
        section_id=bookmark.section_id,
        char_offset=bookmark.char_offset,
        locator=bookmark.locator,
        label=bookmark.label,
    )


def _next_after_ordinal(
    *,
    sections: list[DocumentSection],
    document_section_count: int,
    page_limit: int,
) -> int | None:
    if not sections or len(sections) < page_limit:
        return None
    ordinal = sections[-1].ordinal
    if ordinal >= document_section_count - 1:
        return None
    return ordinal


__all__ = [
    "ReaderAccessDenied",
    "ReaderBookmarkNotFound",
    "ReaderPositionInvalid",
    "ReaderService",
]
