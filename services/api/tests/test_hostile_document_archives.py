import zipfile
from pathlib import Path

import pytest

from bukmatika.processing.parsers import (
    DocumentParseError,
    DocxDocumentParser,
    EpubDocumentParser,
)


def test_epub_rejects_duplicate_normalized_member_paths(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", _epub_container())
        archive.writestr("OEBPS/content.opf", _epub_package("chapter.xhtml"))
        archive.writestr("OEBPS/chapter.xhtml", _chapter("Canonical"))
        archive.writestr("OEBPS/./chapter.xhtml", _chapter("Shadow"))

    with pytest.raises(DocumentParseError, match="duplicate normalized archive paths"):
        EpubDocumentParser().parse(path, max_bytes=64_000)


def test_epub_rejects_unsafe_member_path_even_when_not_in_spine(tmp_path: Path) -> None:
    path = tmp_path / "traversal.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", _epub_container())
        archive.writestr("OEBPS/content.opf", _epub_package("chapter.xhtml"))
        archive.writestr("OEBPS/chapter.xhtml", _chapter("Safe chapter"))
        archive.writestr("../outside.txt", "must never be accepted")

    with pytest.raises(DocumentParseError, match="unsafe archive path"):
        EpubDocumentParser().parse(path, max_bytes=64_000)


def test_epub_rejects_external_spine_resource_reference(tmp_path: Path) -> None:
    path = tmp_path / "external.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", _epub_container())
        archive.writestr(
            "OEBPS/content.opf",
            _epub_package("https://example.invalid/chapter.xhtml"),
        )

    with pytest.raises(DocumentParseError, match="external resource reference"):
        EpubDocumentParser().parse(path, max_bytes=64_000)


def test_epub_rejects_compressed_expansion_beyond_processing_budget(tmp_path: Path) -> None:
    path = tmp_path / "expanded.epub"
    large_text = "A" * 32_000
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", _epub_container())
        archive.writestr("OEBPS/content.opf", _epub_package("chapter.xhtml"))
        archive.writestr("OEBPS/chapter.xhtml", _chapter(large_text))

    assert path.stat().st_size < 8_192
    with pytest.raises(DocumentParseError, match="expanded content exceeds processing byte limit"):
        EpubDocumentParser().parse(path, max_bytes=8_192)


def test_epub_rejects_archive_member_count_over_limit_before_member_map(tmp_path: Path) -> None:
    path = tmp_path / "too-many.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", _epub_container())
        archive.writestr("OEBPS/content.opf", _epub_package("chapter.xhtml"))
        archive.writestr("OEBPS/chapter.xhtml", _chapter("Readable chapter"))
        archive.writestr("extras/one.txt", "1")
        archive.writestr("extras/two.txt", "2")
        archive.writestr("extras/three.txt", "3")

    assert path.stat().st_size < 64_000
    with pytest.raises(DocumentParseError, match="archive member count exceeds configured limit"):
        EpubDocumentParser(max_archive_members=5).parse(path, max_bytes=64_000)


def test_epub_accepts_archive_at_exact_member_limit(tmp_path: Path) -> None:
    path = tmp_path / "exact-limit.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", _epub_container())
        archive.writestr("OEBPS/content.opf", _epub_package("chapter.xhtml"))
        archive.writestr("OEBPS/chapter.xhtml", _chapter("Exact EPUB limit"))

    parsed = EpubDocumentParser(max_archive_members=3).parse(path, max_bytes=64_000)

    assert [section.text for section in parsed.sections] == ["Exact EPUB limit"]


def test_docx_rejects_unsafe_member_path_even_when_not_document_xml(tmp_path: Path) -> None:
    path = tmp_path / "traversal.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _docx_document("Safe paragraph"))
        archive.writestr("../../outside.xml", "<outside/>")

    with pytest.raises(DocumentParseError, match="unsafe archive path"):
        DocxDocumentParser().parse(path, max_bytes=64_000)


def test_docx_rejects_duplicate_normalized_member_paths(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _docx_document("Canonical"))
        archive.writestr("word/./document.xml", _docx_document("Shadow"))

    with pytest.raises(DocumentParseError, match="duplicate normalized archive paths"):
        DocxDocumentParser().parse(path, max_bytes=64_000)


def test_docx_rejects_compressed_expansion_beyond_processing_budget(tmp_path: Path) -> None:
    path = tmp_path / "expanded.docx"
    large_text = "B" * 32_000
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", _docx_document(large_text))

    assert path.stat().st_size < 4_096
    with pytest.raises(DocumentParseError, match="expanded content exceeds processing byte limit"):
        DocxDocumentParser().parse(path, max_bytes=4_096)


def test_docx_rejects_archive_member_count_over_limit_before_member_map(tmp_path: Path) -> None:
    path = tmp_path / "too-many.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _docx_document("Readable paragraph"))
        archive.writestr("docProps/one.xml", "<one/>")
        archive.writestr("docProps/two.xml", "<two/>")
        archive.writestr("docProps/three.xml", "<three/>")

    assert path.stat().st_size < 64_000
    with pytest.raises(DocumentParseError, match="archive member count exceeds configured limit"):
        DocxDocumentParser(max_archive_members=3).parse(path, max_bytes=64_000)


def test_docx_accepts_archive_at_exact_member_limit(tmp_path: Path) -> None:
    path = tmp_path / "exact-limit.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _docx_document("Exact DOCX limit"))

    parsed = DocxDocumentParser(max_archive_members=1).parse(path, max_bytes=64_000)

    assert [section.text for section in parsed.sections] == ["Exact DOCX limit"]


@pytest.mark.parametrize("parser_type", [EpubDocumentParser, DocxDocumentParser])
def test_archive_backed_parsers_reject_nonpositive_member_limits(
    parser_type: type[EpubDocumentParser] | type[DocxDocumentParser],
) -> None:
    with pytest.raises(ValueError, match="max_archive_members must be positive"):
        parser_type(max_archive_members=0)


def _epub_container() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
      <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
      </rootfiles>
    </container>
    """


def _epub_package(href: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <manifest>
        <item id="chapter" href="{href}" media-type="application/xhtml+xml"/>
      </manifest>
      <spine><itemref idref="chapter"/></spine>
    </package>
    """


def _chapter(text: str) -> str:
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>'
        + text
        + "</p></body></html>"
    )


def _docx_document(text: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body>
    </w:document>
    """
