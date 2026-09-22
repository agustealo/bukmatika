from pathlib import Path

import pytest

from bukmatika.processing.chunking import chunk_sections
from bukmatika.processing.parsers import (
    DocumentParseError,
    HtmlDocumentParser,
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
