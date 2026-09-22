import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import Settings
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, StoredObject, Work
from bukmatika.processing import (
    DocumentProcessingService,
    HtmlDocumentParser,
    ParserRegistry,
    TextDocumentParser,
    UnsupportedDocumentFormat,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_stored_asset(
    session: AsyncSession,
    store: LocalObjectStore,
    tmp_path: Path,
    *,
    suffix: str,
    format_name: str,
    payload: bytes,
    media_type: str,
) -> Asset:
    sha256 = hashlib.sha256(payload).hexdigest()
    temp = tmp_path / f"seed-{suffix}.part"
    temp.write_bytes(payload)
    stored_file = await store.commit(temp, sha256=sha256, format_name=format_name)

    work = Work(
        canonical_title=f"Processing Work {suffix}",
        normalized_title=f"processing work {suffix}",
    )
    session.add(work)
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title=f"Processing Work {suffix}",
        language="en",
    )
    session.add(edition)
    stored = StoredObject(
        sha256=sha256,
        storage_key=stored_file.storage_key,
        byte_size=len(payload),
        media_type=media_type,
    )
    session.add(stored)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format=format_name,
        media_type=media_type,
        remote_url=f"https://example.org/{suffix}",
        stored_object_id=stored.id,
        byte_size=len(payload),
    )
    session.add(asset)
    await session.flush()
    return asset


def _service(session: AsyncSession, store: LocalObjectStore, tmp_path: Path) -> DocumentProcessingService:
    settings = Settings(
        storage_root=tmp_path,
        acquisition_worker_enabled=False,
        processing_max_bytes=1_048_576,
        processing_timeout_seconds=5,
    )
    return DocumentProcessingService(
        ParserRegistry((TextDocumentParser(), HtmlDocumentParser())),
        store,
        settings,
        session_scope_factory=_scope(session),
    )


async def test_process_text_is_idempotent_and_search_returns_coordinates(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    asset = await _seed_stored_asset(
        session,
        store,
        tmp_path,
        suffix="txt-1",
        format_name="TXT",
        payload=(
            b"Mariners mapped obsidian navigation routes across the old world.\n\n"
            b"A second passage discusses agricultural exchange and navigation evidence."
        ),
        media_type="text/plain",
    )
    service = _service(session, store, tmp_path)

    first = await service.process_asset(asset.id)
    second = await service.process_asset(asset.id)
    result = await service.search_document(
        first.document_id,
        query="obsidian navigation",
        limit=10,
    )

    assert first.document_id == second.document_id
    assert await session.scalar(select(func.count()).select_from(Document)) == 1
    assert await session.scalar(select(func.count()).select_from(DocumentSection)) == 2
    assert await session.scalar(select(func.count()).select_from(DocumentChunk)) == 2
    assert result.items
    hit = result.items[0]
    assert hit.locator == {"page": 1, "section": 1}
    section = await session.get(DocumentSection, hit.section_id)
    assert section is not None
    assert section.text[hit.char_start : hit.char_end] == hit.text


async def test_unregistered_format_is_not_fake_processed(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    asset = await _seed_stored_asset(
        session,
        store,
        tmp_path,
        suffix="pdf-1",
        format_name="PDF",
        payload=b"%PDF-1.7\nnot parsed in phase three foundation",
        media_type="application/pdf",
    )
    service = _service(session, store, tmp_path)

    with pytest.raises(UnsupportedDocumentFormat, match="No document parser"):
        await service.process_asset(asset.id)

    assert await session.scalar(select(func.count()).select_from(Document)) == 0


async def test_stored_object_read_boundary_rejects_traversal(tmp_path: Path) -> None:
    store = LocalObjectStore(tmp_path)

    with pytest.raises(ValueError, match="canonical object namespace"):
        await store.resolve_path("../outside.txt")
    with pytest.raises(ValueError, match="canonical object namespace"):
        await store.resolve_path("objects/../../outside.txt")
