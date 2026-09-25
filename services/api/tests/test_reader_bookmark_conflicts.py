from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from test_reader import _scope, _seed_reader_document

from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.reader_bookmarks import ReaderBookmarkConflict
from bukmatika.reader import BookmarkCreate, ReaderService
from bukmatika.reader.domain import BookmarkRemoveRequest
from bukmatika.reader.routes import remove_bookmark as remove_bookmark_route


async def test_stale_bookmark_revision_cannot_delete_newer_bookmark(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="bookmark-conflict")
    service = ReaderService(session_scope_factory=_scope(session))
    created = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=sections[1].id,
            char_offset=3,
            label="Initial label",
        ),
    )

    newer = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=sections[1].id,
            char_offset=3,
            label="Newer label",
        ),
    )
    assert newer.bookmark_id == created.bookmark_id
    assert newer.updated_at > created.updated_at

    with pytest.raises(ReaderBookmarkConflict, match="changed after this removal started"):
        await service.remove_bookmark(
            principal_id=entry.principal_id,
            library_entry_id=entry.id,
            document_id=document.id,
            bookmark_id=created.bookmark_id,
            expected_updated_at=created.updated_at,
        )

    reopened = await service.open_reader(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=10,
    )
    persisted = next(
        bookmark
        for bookmark in reopened.bookmarks
        if bookmark.bookmark_id == created.bookmark_id
    )
    assert persisted.label == "Newer label"
    assert persisted.updated_at == newer.updated_at


def test_public_bookmark_remove_request_requires_revision() -> None:
    with pytest.raises(ValidationError):
        BookmarkRemoveRequest.model_validate({})


async def test_public_bookmark_remove_route_maps_stale_revision_to_conflict(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="bookmark-route")
    service = ReaderService(session_scope_factory=_scope(session))
    identity = AuthenticatedPrincipal(
        principal_id=entry.principal_id,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    created = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=sections[0].id,
            char_offset=0,
            label="Initial label",
        ),
    )

    newer = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=sections[0].id,
            char_offset=0,
            label="Canonical newer label",
        ),
    )
    assert newer.updated_at > created.updated_at

    with pytest.raises(HTTPException) as stale_revision:
        await remove_bookmark_route(
            library_entry_id=entry.id,
            document_id=document.id,
            bookmark_id=created.bookmark_id,
            remove=BookmarkRemoveRequest(expected_updated_at=created.updated_at),
            identity=identity,
            service=service,
        )
    assert stale_revision.value.status_code == 409
    assert stale_revision.value.detail == {"code": "READER_BOOKMARK_STALE"}


async def test_public_bookmark_remove_route_accepts_current_revision(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="bookmark-current")
    service = ReaderService(session_scope_factory=_scope(session))
    identity = AuthenticatedPrincipal(
        principal_id=entry.principal_id,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    created = await service.add_bookmark(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=BookmarkCreate(
            section_id=sections[0].id,
            char_offset=0,
            label="Current bookmark",
        ),
    )

    response = await remove_bookmark_route(
        library_entry_id=entry.id,
        document_id=document.id,
        bookmark_id=created.bookmark_id,
        remove=BookmarkRemoveRequest(expected_updated_at=created.updated_at),
        identity=identity,
        service=service,
    )
    assert response.status_code == 204

    reopened = await service.open_reader(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=10,
    )
    assert all(bookmark.bookmark_id != created.bookmark_id for bookmark in reopened.bookmarks)
