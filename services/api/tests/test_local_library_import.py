import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import Settings
from bukmatika.library.local_import import LocalImportError, LocalLibraryImportService
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    InteractionEvent,
    LibraryEntry,
    Principal,
    RightsDecision,
    SourceRecord,
    StoredObject,
    Work,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


async def _stream(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


def _envelope(
    payload: bytes,
    *,
    filename: str = "field-notes.txt",
    title: str | None = "Field Notes",
    author: str | None = "Local Reader",
    media_type: str | None = "text/plain",
) -> tuple[bytes, bytes]:
    metadata = json.dumps(
        {
            "schema_version": 1,
            "filename": filename,
            "title": title,
            "author": author,
            "reported_media_type": media_type,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return metadata + b"\n", payload


async def _principal(session: AsyncSession, label: str) -> UUID:
    principal = Principal(kind="local", external_subject=f"local-import-{label}-{uuid4().hex}")
    session.add(principal)
    await session.flush()
    return principal.id


def _service(
    session: AsyncSession,
    tmp_path: Path,
    *,
    max_bytes: int = 1024 * 1024,
) -> LocalLibraryImportService:
    return LocalLibraryImportService(
        storage=LocalObjectStore(tmp_path / "store"),
        settings=Settings(
            acquisition_max_bytes=max_bytes,
            processing_max_bytes=max_bytes,
        ),
        session_scope_factory=_scope(session),
    )


async def test_local_import_creates_private_canonical_asset_without_acquisition(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal_id = await _principal(session, "canonical")
    payload = b"Local library text that should become canonical reader content.\n"
    metadata, book_bytes = _envelope(payload)

    result = await _service(session, tmp_path).import_stream(
        principal_id=principal_id,
        stream=_stream(metadata[:19], metadata[19:] + book_bytes[:8], book_bytes[8:]),
    )

    assert result.idempotent is False
    assert result.processing_required is True
    assert result.document_id is None
    assert result.title == "Field Notes"
    assert result.authors == ["Local Reader"]
    assert result.format == "TXT"
    assert result.media_type == "text/plain"
    assert result.rights_state == "unknown"
    assert result.private_retention_only is True

    asset = await session.get(Asset, result.asset_id)
    assert asset is not None
    assert asset.remote_url is None
    assert asset.stored_object_id is not None
    assert asset.byte_size == len(payload)

    stored = await session.get(StoredObject, asset.stored_object_id)
    assert stored is not None
    assert stored.sha256 == result.sha256
    assert stored.byte_size == len(payload)
    assert (tmp_path / "store" / stored.storage_key).read_bytes() == payload

    library_entry = await session.get(LibraryEntry, result.library_entry_id)
    assert library_entry is not None
    assert library_entry.principal_id == principal_id
    assert library_entry.edition_id == result.edition_id

    acquisition_count = await session.scalar(
        select(func.count()).select_from(Acquisition).where(Acquisition.asset_id == result.asset_id)
    )
    assert acquisition_count == 0

    decision = await session.scalar(
        select(RightsDecision).where(
            RightsDecision.subject_type == "asset",
            RightsDecision.subject_id == result.asset_id,
        )
    )
    assert decision is not None
    assert decision.rights_state == "unknown"
    assert decision.policy_version == "local-import-private-v1"
    assert decision.permissions["retain"] is True
    assert decision.permissions["process"] is True
    assert decision.permissions["ocr"] is True
    assert decision.permissions["download"] is False
    assert decision.permissions["export"] is False
    assert decision.permissions["share"] is False

    source = await session.scalar(
        select(SourceRecord).where(SourceRecord.provider == "local-import")
    )
    assert source is not None
    assert source.canonical_url.startswith("bukmatika://local-import/")

    event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == principal_id,
            InteractionEvent.event_type == "library.local_import_stored",
            InteractionEvent.entity_id == result.asset_id,
        )
    )
    assert event is not None
    assert event.context["idempotent"] is False
    assert event.context["format"] == "TXT"


async def test_local_import_is_idempotent_for_same_principal_and_content(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal_id = await _principal(session, "idempotent")
    payload = b"One canonical copy even when the same local bytes are submitted again."
    service = _service(session, tmp_path)

    metadata, book_bytes = _envelope(payload, title="Canonical Local Title", author="First Author")
    first = await service.import_stream(
        principal_id=principal_id,
        stream=_stream(metadata, book_bytes),
    )

    second_metadata, second_bytes = _envelope(
        payload,
        title="Do Not Overwrite Canonical Metadata",
        author="Second Author",
    )
    second = await service.import_stream(
        principal_id=principal_id,
        stream=_stream(second_metadata, second_bytes),
    )

    assert second.idempotent is True
    assert second.asset_id == first.asset_id
    assert second.library_entry_id == first.library_entry_id
    assert second.work_id == first.work_id
    assert second.edition_id == first.edition_id
    assert second.title == "Canonical Local Title"
    assert second.authors == ["First Author"]

    work = await session.get(Work, first.work_id)
    assert work is not None
    assert work.canonical_title == "Canonical Local Title"

    asset_count = await session.scalar(
        select(func.count()).select_from(Asset).where(Asset.id == first.asset_id)
    )
    entry_count = await session.scalar(
        select(func.count()).select_from(LibraryEntry).where(
            LibraryEntry.principal_id == principal_id,
            LibraryEntry.work_id == first.work_id,
        )
    )
    source_count = await session.scalar(
        select(func.count())
        .select_from(SourceRecord)
        .where(SourceRecord.provider == "local-import")
    )
    assert asset_count == 1
    assert entry_count == 1
    assert source_count == 1

    events = (
        await session.scalars(
            select(InteractionEvent)
            .where(
                InteractionEvent.principal_id == principal_id,
                InteractionEvent.event_type == "library.local_import_stored",
                InteractionEvent.entity_id == first.asset_id,
            )
            .order_by(InteractionEvent.occurred_at)
        )
    ).all()
    assert [event.context["idempotent"] for event in events] == [False, True]


async def test_same_bytes_for_different_principals_share_storage_not_asset_rights(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    first_principal = await _principal(session, "one")
    second_principal = await _principal(session, "two")
    payload = b"Shared physical bytes, isolated catalog ownership and rights decisions."
    metadata, book_bytes = _envelope(payload, title="Shared Bytes")
    service = _service(session, tmp_path)

    first = await service.import_stream(
        principal_id=first_principal,
        stream=_stream(metadata, book_bytes),
    )
    second = await service.import_stream(
        principal_id=second_principal,
        stream=_stream(metadata, book_bytes),
    )

    assert second.sha256 == first.sha256
    assert second.asset_id != first.asset_id
    assert second.work_id != first.work_id
    assert second.edition_id != first.edition_id
    assert second.library_entry_id != first.library_entry_id

    first_asset = await session.get(Asset, first.asset_id)
    second_asset = await session.get(Asset, second.asset_id)
    assert first_asset is not None and second_asset is not None
    assert first_asset.stored_object_id == second_asset.stored_object_id

    stored_count = await session.scalar(
        select(func.count()).select_from(StoredObject).where(StoredObject.sha256 == first.sha256)
    )
    rights_count = await session.scalar(
        select(func.count()).select_from(RightsDecision).where(
            RightsDecision.subject_type == "asset",
            RightsDecision.subject_id.in_([first.asset_id, second.asset_id]),
        )
    )
    assert stored_count == 1
    assert rights_count == 2


async def test_local_import_rejects_spoofed_pdf_before_catalog_mutation(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal_id = await _principal(session, "spoof")
    source_count_before = await session.scalar(select(func.count()).select_from(SourceRecord))
    work_count_before = await session.scalar(select(func.count()).select_from(Work))
    metadata, payload = _envelope(
        b"This is plain text, not a PDF.",
        filename="spoofed.pdf",
        media_type="application/pdf",
    )

    with pytest.raises(LocalImportError) as caught:
        await _service(session, tmp_path).import_stream(
            principal_id=principal_id,
            stream=_stream(metadata, payload),
        )

    assert caught.value.code == "LOCAL_IMPORT_FORMAT_VERIFICATION_FAILED"
    source_count_after = await session.scalar(select(func.count()).select_from(SourceRecord))
    work_count_after = await session.scalar(select(func.count()).select_from(Work))
    assert source_count_after == source_count_before
    assert work_count_after == work_count_before


async def test_local_import_rejects_unsafe_filename_and_oversized_payload(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal_id = await _principal(session, "bounds")
    unsafe_metadata, unsafe_payload = _envelope(
        b"text",
        filename="../outside.txt",
    )
    with pytest.raises(LocalImportError) as unsafe:
        await _service(session, tmp_path).import_stream(
            principal_id=principal_id,
            stream=_stream(unsafe_metadata, unsafe_payload),
        )
    assert unsafe.value.code == "LOCAL_IMPORT_FILENAME_INVALID"

    large_metadata, large_payload = _envelope(b"0123456789", filename="bounded.txt")
    with pytest.raises(LocalImportError) as oversized:
        await _service(session, tmp_path, max_bytes=8).import_stream(
            principal_id=principal_id,
            stream=_stream(large_metadata, large_payload),
        )
    assert oversized.value.code == "LOCAL_IMPORT_TOO_LARGE"
