import re
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar, Protocol

from bukmatika.processing.domain import ParsedDocument, ParsedSection


class DocumentParseError(RuntimeError):
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
        try:
            html = payload.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise DocumentParseError("HTML document is not valid UTF-8") from exc

        extractor = _ReadableHtmlExtractor()
        try:
            extractor.feed(html)
            extractor.close()
        except Exception as exc:
            raise DocumentParseError("HTML document could not be parsed") from exc

        sections: list[ParsedSection] = []
        for ordinal, (tag, text) in enumerate(extractor.blocks):
            sections.append(
                ParsedSection(
                    ordinal=ordinal,
                    heading=text if tag in _ReadableHtmlExtractor.heading_tags else None,
                    locator={"section": ordinal + 1, "element": tag},
                    text=text,
                )
            )
        if not sections:
            raise DocumentParseError("HTML document contains no readable text")
        return ParsedDocument(
            parser_name=self.name,
            parser_version=self.version,
            sections=tuple(sections),
        )


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


def _read_bounded(path: Path, *, max_bytes: int) -> bytes:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DocumentParseError("Document source could not be inspected") from exc
    if size > max_bytes:
        raise DocumentParseError("Document exceeds configured processing byte limit")
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
