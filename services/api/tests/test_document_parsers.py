import zipfile
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from bukmatika.processing.chunking import chunk_sections
from bukmatika.processing.parsers import (
    DocumentParseError,
    DocumentRequiresOCR,
    EpubDocumentParser,
    HtmlDocumentParser,
    PdfDocumentParser,
    TextDocumentParser,
)


def test_text_parser_preserves_page_and_section_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "book.txt"
    path.write_bytes(
        b"\xef\xbb\xbfFirst paragraph.\n\nSecond paragraph.\fThird page paragraph."
    )

    parsed = TextDocumentParser().parse(path, max_bytes=1024)

    assert [section.text for section in parsed.sections] == [
        "First paragraph.",
        "Second paragraph.",
        "Third page paragraph.",
    ]
    assert parsed.sections[0].locator == {"page": 1, "section": 1}
    assert parsed.sections[2].locator == {"page": 2, "section": 3}


def test_text_parser_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes(b"valid prefix\xffinvalid")

    with pytest.raises(DocumentParseError, match="valid UTF-8"):
        TextDocumentParser().parse(path, max_bytes=1024)


def test_html_parser_ignores_non_readable_content_and_preserves_blocks(tmp_path: Path) -> None:
    path = tmp_path / "book.html"
    path.write_text(
        """
        <html><head><title>Ignored title</title></head><body>
          <h1>Origins</h1>
          <p>First <em>readable</em> paragraph.<br>Second line.</p>
          <script>secretScriptText()</script>
          <style>.hidden { display: none }</style>
          <blockquote>Primary source quotation.</blockquote>
        </body></html>
        """,
        encoding="utf-8",
    )

    parsed = HtmlDocumentParser().parse(path, max_bytes=4096)

    assert [section.text for section in parsed.sections] == [
        "Origins",
        "First readable paragraph.\nSecond line.",
        "Primary source quotation.",
    ]
    assert parsed.sections[0].heading == "Origins"
    assert parsed.sections[0].locator == {"section": 1, "element": "h1"}
    assert all("secretScriptText" not in section.text for section in parsed.sections)


def test_pdf_parser_extracts_text_with_page_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    _write_text_pdf(path, "Hello Bukmatika")

    parsed = PdfDocumentParser().parse(path, max_bytes=16_384)

    assert parsed.parser_name == "pypdf"
    assert len(parsed.sections) == 1
    assert parsed.sections[0].locator == {"page": 1}
    assert parsed.sections[0].text == "Hello Bukmatika"


def test_pdf_without_extractable_text_requires_ocr(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _write_text_pdf(path, None)

    with pytest.raises(DocumentRequiresOCR, match="requires OCR"):
        PdfDocumentParser().parse(path, max_bytes=16_384)


def test_encrypted_pdf_is_not_decrypted(tmp_path: Path) -> None:
    plain_path = tmp_path / "plain.pdf"
    encrypted_path = tmp_path / "encrypted.pdf"
    _write_text_pdf(plain_path, "Protected words")

    reader = PdfReader(plain_path)
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.encrypt("secret")
    with encrypted_path.open("wb") as handle:
        writer.write(handle)

    with pytest.raises(DocumentParseError, match="Encrypted PDF"):
        PdfDocumentParser().parse(encrypted_path, max_bytes=32_768)


def test_epub_parser_preserves_spine_order_and_item_coordinates(tmp_path: Path) -> None:
    path = tmp_path / "book.epub"
    _write_epub(path)

    parsed = EpubDocumentParser().parse(path, max_bytes=65_536)

    assert [section.text for section in parsed.sections] == [
        "First chapter",
        "Opening paragraph.",
        "Second chapter",
        "Closing paragraph.",
    ]
    assert parsed.sections[0].heading == "First chapter"
    assert parsed.sections[0].locator == {
        "spine": 1,
        "item": "OEBPS/chapter-1.xhtml",
        "section": 1,
        "element": "h1",
    }
    assert parsed.sections[2].locator["spine"] == 2
    assert parsed.sections[2].locator["item"] == "OEBPS/chapter-2.xhtml"


def test_epub_rejects_rootfile_path_escape(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.epub"
    container = b"""<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
      <rootfiles>
        <rootfile full-path="../content.opf" media-type="application/oebps-package+xml"/>
      </rootfiles>
    </container>
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)

    with pytest.raises(DocumentParseError, match="unsafe archive path"):
        EpubDocumentParser().parse(path, max_bytes=16_384)


def test_chunk_offsets_resolve_exactly_to_normalized_section_text(tmp_path: Path) -> None:
    path = tmp_path / "long.txt"
    text = " ".join(f"token-{index}" for index in range(220))
    path.write_text(text, encoding="utf-8")
    parsed = TextDocumentParser().parse(path, max_bytes=16_384)

    chunks = chunk_sections(parsed.sections, max_chars=300, overlap_chars=40)

    assert len(chunks) > 1
    section = parsed.sections[0]
    for chunk in chunks:
        assert chunk.section_ordinal == section.ordinal
        assert section.text[chunk.char_start : chunk.char_end] == chunk.text
        assert chunk.char_end > chunk.char_start


def _write_text_pdf(path: Path, text: str | None) -> None:
    content = b""
    if text is not None:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{object_number} 0 obj\n".encode())
        payload.extend(body)
        payload.extend(b"\nendobj\n")

    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(payload)


def _write_epub(path: Path) -> None:
    container = """<?xml version="1.0" encoding="UTF-8"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
      <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
      </rootfiles>
    </container>
    """
    package = """<?xml version="1.0" encoding="UTF-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="book-id">urn:uuid:test-book</dc:identifier>
      </metadata>
      <manifest>
        <item id="c1" href="chapter-1.xhtml" media-type="application/xhtml+xml"/>
        <item id="c2" href="chapter-2.xhtml" media-type="application/xhtml+xml"/>
      </manifest>
      <spine>
        <itemref idref="c1"/>
        <itemref idref="c2"/>
      </spine>
    </package>
    """
    chapter_one = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
      <h1>First chapter</h1><p>Opening paragraph.</p>
      <script>ignored()</script>
    </body></html>"""
    chapter_two = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
      <h1>Second chapter</h1><p>Closing paragraph.</p>
    </body></html>"""

    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter-1.xhtml", chapter_one)
        archive.writestr("OEBPS/chapter-2.xhtml", chapter_two)
