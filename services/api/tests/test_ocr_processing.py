import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import Settings
from bukmatika.persistence.document_models import DocumentProcessingState
from bukmatika.persistence.documents import DocumentRepository
from bukmatika.persistence.models import Asset, Edition, StoredObject, Work
from bukmatika.processing import (
    DocumentProcessingService,
    DocumentRequiresOCR,
    OcrJobWorker,
    OcrQueueService,
    ParserRegistry,
    PdfDocumentParser,
)
from bukmatika.processing.domain import ParsedDocument, ParsedSection
from bukmatika.processing.ocr import OcrExecutionError, TesseractPdfOcrEngine


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _SuccessfulOcrEngine:
    name = "test-ocr"

    def __init__(self) -> None:
        self.calls = 0

    async def recognize(self, path: Path) -> ParsedDocument:
        assert path.is_file()
        self.calls += 1
        return ParsedDocument(
            parser_name=self.name,
            parser_version="1",
            sections=(
                ParsedSection(
                    ordinal=0,
                    heading=None,
                    locator={"page": 1},
                    text="Recovered text from scanned page.",
                ),
            ),
        )


async def _seed_pdf_asset(
    session: AsyncSession,
    store: LocalObjectStore,
    tmp_path: Path,
    *,
    suffix: str,
) -> Asset:
    payload = _blank_pdf_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    temp = tmp_path / f"seed-{suffix}.part"
    temp.write_bytes(payload)
    stored_file = await store.commit(temp, sha256=sha256, format_name="PDF")

    work = Work(
        canonical_title=f"OCR Work {suffix}",
        normalized_title=f"ocr work {suffix}",
    )
    session.add(work)
    await session.flush()
    edition = Edition(work_id=work.id, title=f"OCR Work {suffix}", language="en")
    session.add(edition)
    stored = StoredObject(
        sha256=sha256,
        storage_key=stored_file.storage_key,
        byte_size=len(payload),
        media_type="application/pdf",
    )
    session.add(stored)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.org/{suffix}.pdf",
        stored_object_id=stored.id,
        byte_size=len(payload),
    )
    session.add(asset)
    await session.flush()
    return asset


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        storage_root=tmp_path,
        acquisition_worker_enabled=False,
        processing_max_bytes=1_048_576,
        processing_timeout_seconds=5,
        ocr_worker_poll_seconds=0.01,
        ocr_job_lease_seconds=120,
        ocr_job_heartbeat_seconds=10,
        ocr_max_attempts=2,
        ocr_page_timeout_seconds=5,
        ocr_render_dpi=72,
        ocr_max_pages=10,
        ocr_max_render_pixels=5_000_000,
        ocr_total_text_max_bytes=1_048_576,
    )


async def test_requires_ocr_state_can_be_queued_and_completed_durably(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    settings = _settings(tmp_path)
    asset = await _seed_pdf_asset(session, store, tmp_path, suffix="scan-1")
    processing = DocumentProcessingService(
        ParserRegistry((PdfDocumentParser(),)),
        store,
        settings,
        session_scope_factory=_scope(session),
    )

    with pytest.raises(DocumentRequiresOCR):
        await processing.process_asset(asset.id)

    state = await _processing_state_for_asset(session, asset.id)
    assert state is not None
    assert state.status == "requires_ocr"
    assert state.error_code == "DOCUMENT_REQUIRES_OCR"

    queue = OcrQueueService(settings, session_scope_factory=_scope(session))
    first = await queue.enqueue(asset.id)
    second = await queue.enqueue(asset.id)
    assert first.job_id == second.job_id
    assert first.job_status == "queued"

    engine = _SuccessfulOcrEngine()
    worker = OcrJobWorker(
        engine,
        store,
        settings,
        session_scope_factory=_scope(session),
    )
    assert await worker.run_once() is True
    assert engine.calls == 1

    completed = await queue.get(first.job_id)
    assert completed.job_status == "completed"
    assert completed.processing_status == "completed"
    document = await DocumentRepository(session).get_document_for_asset(asset.id)
    assert document is not None
    assert document.parser_name == "test-ocr"
    assert document.source_sha256 == state.source_sha256


async def test_ocr_worker_fails_closed_when_enqueued_source_changes(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    store = LocalObjectStore(tmp_path)
    settings = _settings(tmp_path)
    asset = await _seed_pdf_asset(session, store, tmp_path, suffix="scan-stale")
    processing = DocumentProcessingService(
        ParserRegistry((PdfDocumentParser(),)),
        store,
        settings,
        session_scope_factory=_scope(session),
    )
    with pytest.raises(DocumentRequiresOCR):
        await processing.process_asset(asset.id)

    queue = OcrQueueService(settings, session_scope_factory=_scope(session))
    queued = await queue.enqueue(asset.id)

    replacement_payload = _blank_pdf_bytes(extra_page=True)
    replacement_sha = hashlib.sha256(replacement_payload).hexdigest()
    replacement_temp = tmp_path / "replacement.part"
    replacement_temp.write_bytes(replacement_payload)
    replacement_file = await store.commit(
        replacement_temp,
        sha256=replacement_sha,
        format_name="PDF",
    )
    replacement = StoredObject(
        sha256=replacement_sha,
        storage_key=replacement_file.storage_key,
        byte_size=len(replacement_payload),
        media_type="application/pdf",
    )
    session.add(replacement)
    await session.flush()
    asset.stored_object_id = replacement.id
    asset.byte_size = len(replacement_payload)
    await session.flush()

    engine = _SuccessfulOcrEngine()
    worker = OcrJobWorker(
        engine,
        store,
        settings,
        session_scope_factory=_scope(session),
    )
    assert await worker.run_once() is True
    assert engine.calls == 0

    failed = await queue.get(queued.job_id)
    assert failed.job_status == "failed"
    assert failed.error_code == "OCR_SOURCE_CHANGED"


async def test_pdfium_external_process_boundary_returns_page_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(_blank_pdf_bytes())
    executable = tmp_path / "fake-tesseract"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('tesseract 5.9.0-test')\n"
        "else:\n"
        "    print('Recovered OCR text from external process.')\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    settings = _settings(tmp_path).model_copy(
        update={"ocr_tesseract_executable": str(executable)}
    )

    parsed = await TesseractPdfOcrEngine(settings).recognize(pdf_path)

    assert parsed.parser_name == "tesseract"
    assert parsed.parser_version == "5.9.0-test"
    assert len(parsed.sections) == 1
    assert parsed.sections[0].locator == {"page": 1}
    assert parsed.sections[0].text == "Recovered OCR text from external process."


async def test_missing_tesseract_engine_fails_explicitly(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(_blank_pdf_bytes())
    settings = _settings(tmp_path).model_copy(
        update={"ocr_tesseract_executable": str(tmp_path / "missing-tesseract")}
    )

    with pytest.raises(OcrExecutionError) as caught:
        await TesseractPdfOcrEngine(settings).recognize(pdf_path)

    assert caught.value.error_code == "OCR_ENGINE_UNAVAILABLE"


async def _processing_state_for_asset(
    session: AsyncSession,
    asset_id: UUID,
) -> DocumentProcessingState | None:
    return await session.scalar(
        select(DocumentProcessingState).where(DocumentProcessingState.asset_id == asset_id)
    )


def _blank_pdf_bytes(*, extra_page: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if extra_page:
        writer.add_blank_page(width=612, height=792)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
