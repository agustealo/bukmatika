import zipfile
from pathlib import Path

import pytest

from bukmatika.acquisition.verification import FormatVerificationError, verify_download


def _verify(path: Path, format_name: str, media_type: str | None) -> None:
    verify_download(
        path,
        expected_format=format_name,
        media_type=media_type,
        archive_max_members=100,
        archive_max_uncompressed_bytes=1024 * 1024,
        archive_max_compression_ratio=100,
    )


def test_pdf_magic_and_content_type_are_verified(tmp_path: Path) -> None:
    valid = tmp_path / "valid.pdf"
    valid.write_bytes(b"%PDF-1.7\nbody")
    _verify(valid, "PDF", "application/pdf")

    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"<html>not a pdf</html>")
    with pytest.raises(FormatVerificationError, match="not a PDF"):
        _verify(invalid, "PDF", "application/pdf")

    with pytest.raises(FormatVerificationError, match="Content-Type"):
        _verify(valid, "PDF", "text/html")


def test_valid_epub_container_passes(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", b"<container/>")
        archive.writestr("OPS/book.xhtml", b"<html><body>book</body></html>")

    _verify(epub, "EPUB", "application/epub+zip")


def test_archive_member_path_traversal_is_quarantinable_failure(tmp_path: Path) -> None:
    docx = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"<document/>")
        archive.writestr("../escape.txt", b"escape")

    with pytest.raises(FormatVerificationError, match="unsafe member path"):
        _verify(
            docx,
            "DOCX",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


def test_high_compression_ratio_is_rejected(tmp_path: Path) -> None:
    docx = tmp_path / "bomb.docx"
    with zipfile.ZipFile(docx, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"<Types/>")
        archive.writestr("word/document.xml", b"A" * 100_000)

    with pytest.raises(FormatVerificationError, match="compression ratio"):
        verify_download(
            docx,
            expected_format="DOCX",
            media_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            archive_max_members=100,
            archive_max_uncompressed_bytes=1024 * 1024,
            archive_max_compression_ratio=10,
        )
