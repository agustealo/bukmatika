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
    ReaderHighlightNotFound,
    ReaderHighlightRecord,
    ReaderNavigationSource,
    ReaderPositionInvalid,
    ReaderRepository,
)
from bukmatika.reader.domain import (
    BookmarkCreate,
    BookmarkResponse,
    HighlightCreate,
    HighlightNoteUpdate,
    HighlightResponse,
    ReaderDocumentResponse,
    ReaderNavigationItem,
    ReaderNavigationKind,
    ReaderNavigationResponse,
    ReaderSection,
    ReadingProgressUpdate,
    ReadingStateResponse,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ReaderService:
    """Consumer reader authority over canonical documents and principal-owned reading state."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def open_reader(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        after_ordinal: int | None,
        limit: int,
    ) -> ReaderDocumentResponse:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
            sections = await repository.sections(
                document_id,
                after_ordinal=after_ordinal,
                limit=limit,
            )
            reading_state = await repository.state_for(library_entry_id, document_id)
            bookmarks = await repository.bookmarks_for(library_entry_id, document_id)
            highlights = await repository.highlights_for(library_entry_id, document_id)
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
                highlights=[_highlight_response(record) for record in highlights],
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

    async def navigation(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
    ) -> ReaderNavigationResponse:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
            sources = await repository.navigation_sources(document_id)
            return _navigation_response(access.document.format, sources)

    async def save_progress(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        update: ReadingProgressUpdate,
    ) -> ReadingStateResponse:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
            state = await repository.save_progress(
                access=access,
                section_id=update.section_id,
                char_offset=update.char_offset,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.READING_PROGRESS_UPDATED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "section_id": str(state.section_id),
                    "char_offset": state.char_offset,
                    "progress_fraction": state.progress_fraction,
                },
            )
            response = _state_response(state)
            if response is None:
                raise RuntimeError("Persisted reading state disappeared")
            return response

    async def add_bookmark(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        create: BookmarkCreate,
    ) -> BookmarkResponse:
        label = _normalized_optional_text(create.label)
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
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
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        bookmark_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
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

    async def add_highlight(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        create: HighlightCreate,
    ) -> HighlightResponse:
        note = _normalized_optional_text(create.note)
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
            record = await repository.add_highlight(
                access=access,
                section_id=create.section_id,
                char_start=create.char_start,
                char_end=create.char_end,
                note=note,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.HIGHLIGHT_ADDED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "highlight_id": str(record.highlight.id),
                    "section_id": str(record.highlight.section_id),
                    "char_start": record.highlight.char_start,
                    "char_end": record.highlight.char_end,
                    "has_note": note is not None,
                },
            )
            return _highlight_response(record)

    async def update_highlight_note(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        highlight_id: UUID,
        update: HighlightNoteUpdate,
    ) -> HighlightResponse:
        note = _normalized_optional_text(update.note)
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
            record = await repository.update_highlight_note(
                access=access,
                highlight_id=highlight_id,
                note=note,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.HIGHLIGHT_NOTE_UPDATED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "highlight_id": str(highlight_id),
                    "has_note": note is not None,
                },
            )
            return _highlight_response(record)

    async def remove_highlight(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        document_id: UUID,
        highlight_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            repository = ReaderRepository(database_session)
            access = await repository.require_access(principal_id, library_entry_id, document_id)
            await repository.remove_highlight(access=access, highlight_id=highlight_id)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.HIGHLIGHT_REMOVED,
                principal_id=access.principal_id,
                entity_type="document",
                entity_id=document_id,
                context={
                    "library_entry_id": str(library_entry_id),
                    "highlight_id": str(highlight_id),
                },
            )


def _navigation_response(
    format_name: str,
    sources: list[ReaderNavigationSource],
) -> ReaderNavigationResponse:
    normalized_format = format_name.upper()
    if normalized_format == "PDF":
        return ReaderNavigationResponse(
            format=normalized_format,
            kind=ReaderNavigationKind.PDF_PAGES,
            items=_pdf_navigation_items(sources),
        )
    if normalized_format == "EPUB":
        return ReaderNavigationResponse(
            format=normalized_format,
            kind=ReaderNavigationKind.EPUB_SPINE,
            items=_epub_navigation_items(sources),
        )
    return ReaderNavigationResponse(format=normalized_format, kind=None, items=[])


def _pdf_navigation_items(sources: list[ReaderNavigationSource]) -> list[ReaderNavigationItem]:
    items: list[ReaderNavigationItem] = []
    seen_pages: set[int] = set()
    for source in sources:
        page = _positive_int(source.locator.get("page"))
        if page is None or page in seen_pages:
            continue
        seen_pages.add(page)
        items.append(
            ReaderNavigationItem(
                key=f"page:{page}",
                label=f"Page {page}",
                section_id=source.section_id,
                section_ordinal=source.ordinal,
                locator=source.locator,
                heading=source.heading,
            )
        )
    return items


def _epub_navigation_items(sources: list[ReaderNavigationSource]) -> list[ReaderNavigationItem]:
    grouped: dict[int, list[ReaderNavigationSource]] = {}
    for source in sources:
        spine = _positive_int(source.locator.get("spine"))
        if spine is None:
            continue
        grouped.setdefault(spine, []).append(source)

    items: list[ReaderNavigationItem] = []
    for spine in sorted(grouped):
        group = grouped[spine]
        target = group[0]
        heading = next(
            (
                source.heading.strip()
                for source in group
                if source.heading is not None and source.heading.strip()
            ),
            None,
        )
        items.append(
            ReaderNavigationItem(
                key=f"spine:{spine}",
                label=heading or f"Section {spine}",
                section_id=target.section_id,
                section_ordinal=target.ordinal,
                locator=target.locator,
                heading=heading,
            )
        )
    return items


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


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
        section_ordinal=state.section_ordinal,
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


def _highlight_response(record: ReaderHighlightRecord) -> HighlightResponse:
    highlight = record.highlight
    return HighlightResponse(
        highlight_id=highlight.id,
        section_id=highlight.section_id,
        char_start=highlight.char_start,
        char_end=highlight.char_end,
        locator=highlight.locator,
        text=record.section.text[highlight.char_start : highlight.char_end],
        note=highlight.note,
    )


def _normalized_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


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
    "ReaderHighlightNotFound",
    "ReaderPositionInvalid",
    "ReaderService",
]
