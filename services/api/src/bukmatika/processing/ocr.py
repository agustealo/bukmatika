import asyncio
import os
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

from bukmatika.config import Settings
from bukmatika.processing.domain import ParsedDocument, ParsedSection


class OcrExecutionError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


class _ProcessOutputLimit(RuntimeError):
    pass


class TesseractPdfOcrEngine:
    """Render PDF pages with PDFium and OCR each page through bounded Tesseract CLI calls."""

    name = "tesseract"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def recognize(self, path: Path) -> ParsedDocument:
        page_count = await asyncio.to_thread(_pdf_page_count, path)
        if page_count < 1:
            raise OcrExecutionError("OCR_PDF_EMPTY", "PDF contains no pages")
        if page_count > self._settings.ocr_max_pages:
            raise OcrExecutionError(
                "OCR_PAGE_LIMIT_EXCEEDED",
                f"PDF has {page_count} pages; configured OCR limit is {self._settings.ocr_max_pages}",
            )

        version = await self._engine_version()
        sections: list[ParsedSection] = []
        with tempfile.TemporaryDirectory(prefix="bukmatika-ocr-") as temp_directory:
            root = Path(temp_directory)
            for page_index in range(page_count):
                image_path = root / f"page-{page_index + 1:06d}.png"
                try:
                    await asyncio.to_thread(
                        _render_pdf_page,
                        path,
                        page_index,
                        image_path,
                        self._settings.ocr_render_dpi,
                    )
                    text = await self._recognize_image(image_path)
                finally:
                    try:
                        image_path.unlink()
                    except FileNotFoundError:
                        pass
                normalized = _normalize_ocr_text(text)
                if not normalized:
                    continue
                sections.append(
                    ParsedSection(
                        ordinal=len(sections),
                        heading=None,
                        locator={"page": page_index + 1},
                        text=normalized,
                    )
                )

        if not sections:
            raise OcrExecutionError("OCR_NO_TEXT", "OCR produced no readable text")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=version,
            sections=tuple(sections),
        )

    async def _engine_version(self) -> str:
        stdout, stderr, return_code = await self._run_process(
            [self._settings.ocr_tesseract_executable, "--version"],
            timeout_seconds=min(self._settings.ocr_page_timeout_seconds, 10.0),
        )
        if return_code != 0:
            detail = _safe_process_detail(stderr or stdout)
            raise OcrExecutionError(
                "OCR_ENGINE_UNAVAILABLE",
                f"Tesseract version probe failed: {detail}",
            )
        first_line = (stdout or stderr).decode("utf-8", errors="replace").splitlines()
        if not first_line:
            raise OcrExecutionError(
                "OCR_ENGINE_UNAVAILABLE",
                "Tesseract version probe returned no version information",
            )
        rendered = first_line[0].strip()
        return rendered.removeprefix("tesseract ")[:64] or "unknown"

    async def _recognize_image(self, image_path: Path) -> bytes:
        command = [
            self._settings.ocr_tesseract_executable,
            str(image_path),
            "-",
            "-l",
            self._settings.ocr_language,
            "--psm",
            str(self._settings.ocr_page_segmentation_mode),
        ]
        stdout, stderr, return_code = await self._run_process(
            command,
            timeout_seconds=self._settings.ocr_page_timeout_seconds,
        )
        if return_code != 0:
            raise OcrExecutionError(
                "OCR_ENGINE_FAILED",
                f"Tesseract failed: {_safe_process_detail(stderr)}",
            )
        return stdout

    async def _run_process(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
    ) -> tuple[bytes, bytes, int]:
        env = _ocr_environment(self._settings)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise OcrExecutionError(
                "OCR_ENGINE_UNAVAILABLE",
                f"Tesseract executable could not be started: {type(exc).__name__}",
            ) from exc

        stdout_task = asyncio.create_task(
            _read_limited(process.stdout, self._settings.ocr_stdout_max_bytes)
        )
        stderr_task = asyncio.create_task(
            _read_limited(process.stderr, self._settings.ocr_stderr_max_bytes)
        )
        wait_task = asyncio.create_task(process.wait())
        try:
            stdout, stderr, return_code = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task, wait_task),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            _cancel_tasks(stdout_task, stderr_task, wait_task)
            raise OcrExecutionError(
                "OCR_ENGINE_TIMEOUT",
                "Tesseract exceeded the configured per-page timeout",
            ) from exc
        except _ProcessOutputLimit as exc:
            process.kill()
            await process.wait()
            _cancel_tasks(stdout_task, stderr_task, wait_task)
            raise OcrExecutionError(
                "OCR_OUTPUT_LIMIT_EXCEEDED",
                "Tesseract output exceeded the configured byte limit",
            ) from exc
        return stdout, stderr, return_code


async def _read_limited(stream: asyncio.StreamReader | None, limit: int) -> bytes:
    if stream is None:
        return b""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65_536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise _ProcessOutputLimit
        chunks.append(chunk)


def _cancel_tasks(*tasks: asyncio.Task[object]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()


def _pdf_page_count(path: Path) -> int:
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()
    except Exception as exc:
        raise OcrExecutionError("OCR_PDF_OPEN_FAILED", "PDFium could not open the PDF") from exc


def _render_pdf_page(path: Path, page_index: int, destination: Path, dpi: int) -> None:
    try:
        document = pdfium.PdfDocument(str(path))
        try:
            page = document[page_index]
            try:
                bitmap = page.render(scale=dpi / 72.0)
                try:
                    image = bitmap.to_pil()
                    try:
                        image.save(destination, format="PNG")
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            document.close()
    except OcrExecutionError:
        raise
    except Exception as exc:
        raise OcrExecutionError(
            "OCR_PAGE_RENDER_FAILED",
            f"PDF page {page_index + 1} could not be rendered",
        ) from exc


def _ocr_environment(settings: Settings) -> dict[str, str]:
    allowed = ("PATH", "SystemRoot", "WINDIR", "HOME", "TEMP", "TMP")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["LANG"] = "C.UTF-8"
    env["LC_ALL"] = "C.UTF-8"
    if settings.ocr_tessdata_prefix is not None:
        env["TESSDATA_PREFIX"] = str(settings.ocr_tessdata_prefix)
    return env


def _safe_process_detail(payload: bytes) -> str:
    text = payload.decode("utf-8", errors="replace").strip()
    return text[:2000] if text else "no diagnostic output"


def _normalize_ocr_text(payload: bytes) -> str:
    value = payload.decode("utf-8", errors="strict").replace("\f", "\n")
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()
