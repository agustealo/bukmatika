from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from test_reader import _scope, _seed_reader_document

from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.reader_highlight_notes import ReaderHighlightConflict
from bukmatika.reader import HighlightCreate, HighlightNoteUpdate, ReaderService
from bukmatika.reader.domain import HighlightNoteRequest
from bukmatika.reader.routes import update_highlight_note as update_highlight_note_route


async def test_stale_highlight_note_revision_cannot_overwrite_newer_note(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="note-conflict")
    service = ReaderService(session_scope_factory=_scope(session))
    created = await service.add_highlight(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=HighlightCreate(
            section_id=sections[1].id,
            char_start=0,
            char_end=12,
            note="Initial note",
        ),
    )
    original_revision = created.updated_at

    newer = await service.update_highlight_note(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        highlight_id=created.highlight_id,
        update=HighlightNoteUpdate(
            note="Newer note",
            expected_updated_at=original_revision,
        ),
    )
    assert newer.note == "Newer note"
    assert newer.updated_at > original_revision

    with pytest.raises(ReaderHighlightConflict, match="changed after this edit started"):
        await service.update_highlight_note(
            principal_id=entry.principal_id,
            library_entry_id=entry.id,
            document_id=document.id,
            highlight_id=created.highlight_id,
            update=HighlightNoteUpdate(
                note="Stale overwrite",
                expected_updated_at=original_revision,
            ),
        )

    reopened = await service.open_reader(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=10,
    )
    persisted = next(
        highlight
        for highlight in reopened.highlights
        if highlight.highlight_id == created.highlight_id
    )
    assert persisted.note == "Newer note"
    assert persisted.updated_at == newer.updated_at


def test_public_note_request_requires_revision() -> None:
    with pytest.raises(ValidationError):
        HighlightNoteRequest.model_validate({"note": "No revision"})


async def test_public_note_route_maps_stale_revision_to_conflict(
    session: AsyncSession,
) -> None:
    entry, document, sections = await _seed_reader_document(session, suffix="note-route")
    service = ReaderService(session_scope_factory=_scope(session))
    identity = AuthenticatedPrincipal(
        principal_id=entry.principal_id,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    created = await service.add_highlight(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        create=HighlightCreate(
            section_id=sections[0].id,
            char_start=0,
            char_end=10,
            note="Initial note",
        ),
    )

    await service.update_highlight_note(
        principal_id=entry.principal_id,
        library_entry_id=entry.id,
        document_id=document.id,
        highlight_id=created.highlight_id,
        update=HighlightNoteUpdate(
            note="Canonical newer note",
            expected_updated_at=created.updated_at,
        ),
    )

    with pytest.raises(HTTPException) as stale_revision:
        await update_highlight_note_route(
            library_entry_id=entry.id,
            document_id=document.id,
            highlight_id=created.highlight_id,
            update=HighlightNoteRequest(
                note="Stale route overwrite",
                expected_updated_at=created.updated_at,
            ),
            identity=identity,
            service=service,
        )
    assert stale_revision.value.status_code == 409
    assert stale_revision.value.detail == {"code": "READER_HIGHLIGHT_STALE"}
