import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import ClassVar, Protocol
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from bukmatika.processing.domain import ParsedDocument, ParsedSection


class DocumentParseError(RuntimeError):
    pass


class DocumentRequiresOCR(DocumentParseError):
    pass


class UnsupportedDocumentFormat(DocumentParseError):
    pass


class DocumentParser(Protocol):
    format_name: str
    name: str
    version: str

    def parse(self, path: Path, *, max_bytes: int) -> ParsedDocument: ...


class ParserRegistry:
    def __init__(self, parsers: tuple[DocumentParser, ...] = ()) -> None:
        self._parsers: dict[str, DocumentParser] = {}
        for parser in parsers:
            self.register(parser)

    def register(self, parser: DocumentParser) -> None:
        key = parser.format_name.upper()
        if key in self._parsers:
            raise ValueError(f"Parser already registered for {key}")
        self._parsers[key] = parser

    def get(self, format_name: str) -> DocumentParser:
        parser = self._parsers.get(format_name.upper())
        if parser is None:
            raise UnsupportedDocumentFormat(
                f"No document parser is registered for {format_name.upper()}"
            )
        return parser


class TextDocumentParser:
    format_name = "TXT"
    name = "builtin-text"
    version = "1"

    def parse(self, path: Path, *, max_bytes: int) -> ParsedDocument:
        payload = _read_bounded(path, max_bytes=max_bytes)
        try:
            text = payload.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise DocumentParseError("Text document is not valid UTF-8") from exc

        sections: list[ParsedSection] = []
        pages = text.split("\f")
        ordinal = 0
        for page_number, page in enumerate(pages, start=1):
            paragraphs = re.split(r"(?:\r?\n)[\t ]*(?:\r?\n)+", page)
            for paragraph in paragraphs:
                normalized = _normalize_text_block(paragraph)
                if not normalized:
                    continue
                sections.append(
                    ParsedSection(
                        ordinal=ordinal,
                        heading=None,
                        locator={"page": page_number, "section": ordinal + 1},
                        text=normalized,
                    )
                )
                ordinal += 1

        if not sections:
            raise DocumentParseError("Text document contains no readable text")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            sections=tuple(sections),
        )


class HtmlDocumentParser:
    format_name = "HTML"
    name = "builtin-html"
    version = "1"

    def parse(self, path: Path, *, max_bytes: int) -> ParsedDocument:
        payload = _read_bounded(path, max_bytes=max_bytes)
        html = _decode_markup(payload, label="HTML document")
        blocks = _extract_readable_html(html, label="HTML document")
        sections = tuple(
            ParsedSection(
                ordinal=ordinal,
                heading=text if tag in _ReadableHtmlExtractor.heading_tags else None,
                locator={"section": ordinal + 1, "element": tag},
                text=text,
            )
            for ordinal, (tag, text) in enumerate(blocks)
        )
        if not sections:
            raise DocumentParseError("HTML document contains no readable text")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            sections=sections,
        )


class PdfDocumentParser:
    format_name = "PDF"
    name = "pypdf"
    version = "1"

    def parse(self, path: Path, *, max_bytes: int) -> ParsedDocument:
        _validate_bounded_file(path, max_bytes=max_bytes)
        try:
            reader = PdfReader(path, strict=False)
        except (OSError, PdfReadError, ValueError) as exc:
            raise DocumentParseError("PDF document could not be opened") from exc

        if reader.is_encrypted:
            raise DocumentParseError("Encrypted PDF documents are not supported")
        if not reader.pages:
            raise DocumentParseError("PDF document contains no pages")

        sections: list[ParsedSection] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:
                raise DocumentParseError(
                    f"PDF page {page_number} could not be text-extracted"
                ) from exc
            normalized = _normalize_text_block(text)
            if not normalized:
                continue
            sections.append(
                ParsedSection(
                    ordinal=len(sections),
                    heading=None,
                    locator={"page": page_number},
                    text=normalized,
                )
            )

        if not sections:
            raise DocumentRequiresOCR("PDF contains no extractable text and requires OCR")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            sections=tuple(sections),
        )


class EpubDocumentParser:
    format_name = "EPUB"
    name = "builtin-epub"
    version = "1"

    _container_path = "META-INF/container.xml"
    _readable_media_types: ClassVar[frozenset[str]] = frozenset(
        {"application/xhtml+xml", "text/html"}
    )

    def parse(self, path: Path, *, max_bytes: int) -> ParsedDocument:
        _validate_bounded_file(path, max_bytes=max_bytes)
        try:
            with zipfile.ZipFile(path) as archive:
                members = _safe_archive_members(archive, label="EPUB")
                budget = _ArchiveReadBudget(max_bytes, label="EPUB")
                container_info = members.get(self._container_path)
                if container_info is None:
                    raise DocumentParseError("EPUB is missing META-INF/container.xml")
                container = _parse_xml(
                    budget.read(archive, container_info),
                    label="EPUB container.xml",
                )
                rootfile_path = _epub_rootfile_path(container)
                rootfile_info = members.get(rootfile_path)
                if rootfile_info is None:
                    raise DocumentParseError("EPUB package document is missing")
                package = _parse_xml(
                    budget.read(archive, rootfile_info),
                    label="EPUB package document",
                )
                manifest = _epub_manifest(package)
                spine = _epub_spine(package)
                sections = _epub_sections(
                    archive=archive,
                    members=members,
                    budget=budget,
                    rootfile_path=rootfile_path,
                    manifest=manifest,
                    spine=spine,
                )
        except DocumentParseError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise DocumentParseError("EPUB document could not be parsed") from exc

        if not sections:
            raise DocumentParseError("EPUB spine contains no readable text")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            sections=tuple(sections),
        )


class DocxDocumentParser:
    format_name = "DOCX"
    name = "builtin-docx"
    version = "1"

    _document_path = "word/document.xml"

    def parse(self, path: Path, *, max_bytes: int) -> ParsedDocument:
        _validate_bounded_file(path, max_bytes=max_bytes)
        try:
            with zipfile.ZipFile(path) as archive:
                members = _safe_archive_members(archive, label="DOCX")
                budget = _ArchiveReadBudget(max_bytes, label="DOCX")
                document_info = members.get(self._document_path)
                if document_info is None:
                    raise DocumentParseError("DOCX is missing word/document.xml")
                document = _parse_xml(
                    budget.read(archive, document_info),
                    label="DOCX document.xml",
                )
                sections = _docx_sections(document)
        except DocumentParseError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise DocumentParseError("DOCX document could not be parsed") from exc

        if not sections:
            raise DocumentParseError("DOCX document contains no readable text")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            sections=tuple(sections),
        )


@dataclass(frozen=True, slots=True)
class _EpubManifestItem:
    href: str
    media_type: str


class _ArchiveReadBudget:
    def __init__(self, max_bytes: int, *, label: str) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._remaining = max_bytes
        self._label = label

    def read(self, archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
        if info.file_size > self._remaining:
            raise DocumentParseError(
                f"{self._label} expanded content exceeds processing byte limit"
            )
        payload = archive.read(info)
        if len(payload) > self._remaining:
            raise DocumentParseError(
                f"{self._label} expanded content exceeds processing byte limit"
            )
        self._remaining -= len(payload)
        return payload


class _ReadableHtmlExtractor(HTMLParser):
    block_tags: ClassVar[frozenset[str]] = frozenset(
        {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre"}
    )
    heading_tags: ClassVar[frozenset[str]] = frozenset(
        {"h1", "h2", "h3", "h4", "h5", "h6"}
    )
    ignored_tags: ClassVar[frozenset[str]] = frozenset(
        {"script", "style", "template", "head", "noscript"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []
        self._ignored_depth = 0
        self._active_tag: str | None = None
        self._active_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in self.ignored_tags:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if self._active_tag is not None:
            if tag == "br":
                self._parts.append("\n")
                return
            self._active_depth += 1
            return
        if tag in self.block_tags:
            self._active_tag = tag
            self._active_depth = 0
            self._parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.ignored_tags:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth or self._active_tag is None:
            return
        if self._active_depth:
            self._active_depth -= 1
            return
        if tag == self._active_tag:
            self._flush_active()

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and self._active_tag is not None:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        if self._active_tag is not None:
            self._flush_active()

    def _flush_active(self) -> None:
        if self._active_tag is None:
            return
        text = _normalize_text_block("".join(self._parts))
        if text:
            self.blocks.append((self._active_tag, text))
        self._active_tag = None
        self._active_depth = 0
        self._parts = []


def _epub_rootfile_path(container: ElementTree.Element) -> str:
    for element in container.iter():
        if _local_name(element.tag) != "rootfile":
            continue
        raw = element.attrib.get("full-path")
        if raw:
            return _normalize_archive_path(raw, label="EPUB")
    raise DocumentParseError("EPUB container has no package rootfile")


def _epub_manifest(package: ElementTree.Element) -> dict[str, _EpubManifestItem]:
    manifest: dict[str, _EpubManifestItem] = {}
    for element in package.iter():
        if _local_name(element.tag) != "item":
            continue
        item_id = element.attrib.get("id")
        href = element.attrib.get("href")
        media_type = element.attrib.get("media-type")
        if item_id and href and media_type:
            manifest[item_id] = _EpubManifestItem(href=href, media_type=media_type)
    if not manifest:
        raise DocumentParseError("EPUB package manifest is empty")
    return manifest


def _epub_spine(package: ElementTree.Element) -> tuple[str, ...]:
    for element in package.iter():
        if _local_name(element.tag) != "spine":
            continue
        identifiers = tuple(
            child.attrib["idref"]
            for child in element
            if _local_name(child.tag) == "itemref" and child.attrib.get("idref")
        )
        if identifiers:
            return identifiers
    raise DocumentParseError("EPUB package spine is empty")


def _epub_sections(
    *,
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    budget: _ArchiveReadBudget,
    rootfile_path: str,
    manifest: dict[str, _EpubManifestItem],
    spine: tuple[str, ...],
) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    package_directory = PurePosixPath(rootfile_path).parent
    for spine_position, item_id in enumerate(spine, start=1):
        item = manifest.get(item_id)
        if item is None:
            raise DocumentParseError(f"EPUB spine references missing manifest item {item_id!r}")
        if item.media_type not in EpubDocumentParser._readable_media_types:
            continue
        member_path = _resolve_archive_reference(package_directory, item.href)
        info = members.get(member_path)
        if info is None:
            raise DocumentParseError(f"EPUB spine item is missing: {member_path}")
        markup = _decode_markup(
            budget.read(archive, info),
            label=f"EPUB spine item {member_path}",
        )
        blocks = _extract_readable_html(markup, label=f"EPUB spine item {member_path}")
        for item_section, (tag, text) in enumerate(blocks, start=1):
            sections.append(
                ParsedSection(
                    ordinal=len(sections),
                    heading=text if tag in _ReadableHtmlExtractor.heading_tags else None,
                    locator={
                        "spine": spine_position,
                        "item": member_path,
                        "section": item_section,
                        "element": tag,
                    },
                    text=text,
                )
            )
    return sections


def _docx_sections(document: ElementTree.Element) -> list[ParsedSection]:
    body = next(
        (element for element in document.iter() if _local_name(element.tag) == "body"),
        None,
    )
    if body is None:
        raise DocumentParseError("DOCX document.xml has no body")

    sections: list[ParsedSection] = []
    paragraph_number = 0
    table_number = 0
    for block_number, child in enumerate(body, start=1):
        child_name = _local_name(child.tag)
        if child_name == "p":
            paragraph_number += 1
            text = _docx_text(child)
            if not text:
                continue
            style = _docx_paragraph_style(child)
            sections.append(
                ParsedSection(
                    ordinal=len(sections),
                    heading=text if _docx_style_is_heading(style) else None,
                    locator={
                        "block": block_number,
                        "type": "paragraph",
                        "paragraph": paragraph_number,
                    },
                    text=text,
                )
            )
            continue
        if child_name != "tbl":
            continue

        table_number += 1
        row_number = 0
        for row in child:
            if _local_name(row.tag) != "tr":
                continue
            row_number += 1
            cells = [
                _docx_cell_text(cell)
                for cell in row
                if _local_name(cell.tag) == "tc"
            ]
            row_text = "\t".join(cell for cell in cells if cell)
            if not row_text:
                continue
            sections.append(
                ParsedSection(
                    ordinal=len(sections),
                    heading=None,
                    locator={
                        "block": block_number,
                        "type": "table_row",
                        "table": table_number,
                        "row": row_number,
                    },
                    text=row_text,
                )
            )
    return sections


def _docx_text(element: ElementTree.Element) -> str:
    parts: list[str] = []
    for descendant in element.iter():
        name = _local_name(descendant.tag)
        if name == "t" and descendant.text:
            parts.append(descendant.text)
        elif name == "tab":
            parts.append("\t")
        elif name in {"br", "cr"}:
            parts.append("\n")
    return _normalize_text_block("".join(parts))


def _docx_cell_text(cell: ElementTree.Element) -> str:
    paragraphs = [
        _docx_text(element)
        for element in cell
        if _local_name(element.tag) == "p"
    ]
    return " / ".join(text for text in paragraphs if text)


def _docx_paragraph_style(paragraph: ElementTree.Element) -> str | None:
    for element in paragraph.iter():
        if _local_name(element.tag) != "pStyle":
            continue
        for attribute, value in element.attrib.items():
            if _local_name(attribute) == "val":
                return value
    return None


def _docx_style_is_heading(style: str | None) -> bool:
    if style is None:
        return False
    normalized = style.replace(" ", "").casefold()
    return normalized.startswith(("heading", "title", "subtitle"))


def _safe_archive_members(
    archive: zipfile.ZipFile,
    *,
    label: str,
) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        normalized = _normalize_archive_path(info.filename, label=label)
        if normalized in members:
            raise DocumentParseError(f"{label} contains duplicate normalized archive paths")
        members[normalized] = info
    return members


def _resolve_archive_reference(base: PurePosixPath, reference: str) -> str:
    split = urlsplit(reference)
    if split.scheme or split.netloc:
        raise DocumentParseError("EPUB spine contains an external resource reference")
    decoded = unquote(split.path).replace("\\", "/")
    combined = posixpath.normpath((base / decoded).as_posix())
    return _normalize_archive_path(combined, label="EPUB")


def _normalize_archive_path(value: str, *, label: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/"))
    path = PurePosixPath(normalized)
    if normalized in {"", "."} or path.is_absolute() or ".." in path.parts:
        raise DocumentParseError(f"{label} contains an unsafe archive path")
    return path.as_posix()


def _parse_xml(payload: bytes, *, label: str) -> ElementTree.Element:
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise DocumentParseError(f"{label} contains disallowed XML declarations")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise DocumentParseError(f"{label} is malformed XML") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_readable_html(html: str, *, label: str) -> list[tuple[str, str]]:
    extractor = _ReadableHtmlExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception as exc:
        raise DocumentParseError(f"{label} could not be parsed") from exc
    return extractor.blocks


def _decode_markup(payload: bytes, *, label: str) -> str:
    try:
        if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
            return payload.decode("utf-16", errors="strict")
        return payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentParseError(f"{label} is not valid UTF-8/UTF-16") from exc


def _validate_bounded_file(path: Path, *, max_bytes: int) -> int:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DocumentParseError("Document source could not be inspected") from exc
    if size > max_bytes:
        raise DocumentParseError("Document exceeds configured processing byte limit")
    return size


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    _validate_bounded_file(path, max_bytes=max_bytes)
    try:
        with path.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except OSError as exc:
        raise DocumentParseError("Document source could not be read") from exc
    if len(payload) > max_bytes:
        raise DocumentParseError("Document exceeds configured processing byte limit")
    return payload


def _normalize_text_block(value: str) -> str:
    lines = [" ".join(line.split()) for line in value.replace("\r\n", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip()
