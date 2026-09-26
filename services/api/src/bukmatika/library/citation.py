from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Contributor, Edition, Identifier, Work, WorkContributor


class CitationExportNotFound(LookupError):
    pass


class CitationFormat(StrEnum):
    CSL_JSON = "csl-json"
    BIBTEX = "bibtex"
    RIS = "ris"


@dataclass(frozen=True, slots=True)
class CitationRecord:
    edition_id: UUID
    title: str
    authors: tuple[str, ...]
    publisher: str | None
    publication_year: int | None
    language: str | None
    edition_statement: str | None
    isbn: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CitationExport:
    content: str
    media_type: str
    filename: str


class CitationExportService:
    """Read-only citation projection over canonical catalog metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def export(
        self,
        *,
        work_id: UUID,
        edition_id: UUID,
        citation_format: CitationFormat,
    ) -> CitationExport:
        record = await self._record(work_id=work_id, edition_id=edition_id)
        if citation_format is CitationFormat.CSL_JSON:
            return CitationExport(
                content=_render_csl_json(record),
                media_type="application/vnd.citationstyles.csl+json",
                filename=_filename(record, "json"),
            )
        if citation_format is CitationFormat.BIBTEX:
            return CitationExport(
                content=_render_bibtex(record),
                media_type="application/x-bibtex",
                filename=_filename(record, "bib"),
            )
        return CitationExport(
            content=_render_ris(record),
            media_type="application/x-research-info-systems",
            filename=_filename(record, "ris"),
        )

    async def _record(self, *, work_id: UUID, edition_id: UUID) -> CitationRecord:
        row = (
            await self._session.execute(
                select(Work, Edition)
                .join(Edition, Edition.work_id == Work.id)
                .where(Work.id == work_id, Edition.id == edition_id)
            )
        ).one_or_none()
        if row is None:
            raise CitationExportNotFound("Edition is not part of this canonical work")
        _, edition = row

        authors = tuple(
            await self._session.scalars(
                select(Contributor.display_name)
                .join(WorkContributor, WorkContributor.contributor_id == Contributor.id)
                .where(
                    WorkContributor.work_id == work_id,
                    WorkContributor.role == "author",
                )
                .order_by(Contributor.display_name, Contributor.id)
            )
        )
        isbn = tuple(
            await self._session.scalars(
                select(Identifier.value)
                .where(
                    Identifier.entity_type == "edition",
                    Identifier.entity_id == edition.id,
                    Identifier.scheme == "isbn",
                )
                .order_by(Identifier.normalized_value, Identifier.id)
            )
        )
        return CitationRecord(
            edition_id=edition.id,
            title=_single_line(edition.title),
            authors=tuple(_single_line(author) for author in authors),
            publisher=_optional_line(edition.publisher),
            publication_year=edition.publication_year,
            language=_optional_line(edition.language),
            edition_statement=_optional_line(edition.edition_statement),
            isbn=tuple(_single_line(value) for value in isbn),
        )


def _render_csl_json(record: CitationRecord) -> str:
    item: dict[str, object] = {
        "id": f"bukmatika:edition:{record.edition_id}",
        "type": "book",
        "title": record.title,
    }
    if record.authors:
        item["author"] = [{"literal": author} for author in record.authors]
    if record.publisher:
        item["publisher"] = record.publisher
    if record.publication_year is not None:
        item["issued"] = {"date-parts": [[record.publication_year]]}
    if record.language:
        item["language"] = record.language
    if record.edition_statement:
        item["edition"] = record.edition_statement
    if record.isbn:
        item["ISBN"] = record.isbn[0]
    return json.dumps([item], ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _render_bibtex(record: CitationRecord) -> str:
    fields: list[tuple[str, str]] = [("title", record.title)]
    if record.authors:
        fields.append(("author", " and ".join(record.authors)))
    if record.publisher:
        fields.append(("publisher", record.publisher))
    if record.publication_year is not None:
        fields.append(("year", str(record.publication_year)))
    if record.isbn:
        fields.append(("isbn", record.isbn[0]))
    if record.language:
        fields.append(("language", record.language))
    if record.edition_statement:
        fields.append(("edition", record.edition_statement))

    body = ",\n".join(f"  {name} = {{{_bibtex_escape(value)}}}" for name, value in fields)
    return f"@book{{{_citation_key(record)},\n{body}\n}}\n"


def _render_ris(record: CitationRecord) -> str:
    lines = ["TY  - BOOK", f"TI  - {record.title}"]
    lines.extend(f"AU  - {author}" for author in record.authors)
    if record.publisher:
        lines.append(f"PB  - {record.publisher}")
    if record.publication_year is not None:
        lines.append(f"PY  - {record.publication_year:04d}")
    if record.language:
        lines.append(f"LA  - {record.language}")
    if record.edition_statement:
        lines.append(f"ET  - {record.edition_statement}")
    lines.extend(f"SN  - {isbn}" for isbn in record.isbn)
    lines.append("ER  -")
    return "\r\n".join(lines) + "\r\n"


def _citation_key(record: CitationRecord) -> str:
    author = record.authors[0] if record.authors else "bukmatika"
    year = str(record.publication_year) if record.publication_year is not None else "nd"
    raw = f"{author}-{year}-{record.title}"
    slug = re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-")
    if not slug:
        slug = "bukmatika"
    return f"{slug[:48]}-{str(record.edition_id)[:8]}"


def _filename(record: CitationRecord, extension: str) -> str:
    title = re.sub(r"[^a-z0-9]+", "-", record.title.casefold()).strip("-") or "citation"
    year = f"-{record.publication_year}" if record.publication_year is not None else ""
    return f"{title[:48]}{year}-{str(record.edition_id)[:8]}.{extension}"


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _optional_line(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _single_line(value)
    return normalized or None


def _bibtex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in value)


__all__ = [
    "CitationExport",
    "CitationExportNotFound",
    "CitationExportService",
    "CitationFormat",
]
