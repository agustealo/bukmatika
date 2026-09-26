import json
import re
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.models import (
    Contributor,
    Edition,
    Identifier,
    LibraryEntry,
    SourceRecord,
    SourceRecordLink,
    Work,
    WorkContributor,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class CitationExportFormat(StrEnum):
    BIBTEX = "bibtex"
    CSL_JSON = "csl-json"
    RIS = "ris"


@dataclass(frozen=True, slots=True)
class CitationRecord:
    library_entry_id: UUID
    work_id: UUID
    edition_id: UUID | None
    title: str
    authors: tuple[str, ...]
    publication_year: int | None
    publisher: str | None
    language: str | None
    edition_statement: str | None
    isbn: str | None
    source_url: str | None


@dataclass(frozen=True, slots=True)
class CitationExport:
    filename: str
    media_type: str
    content: str


class LibraryCitationExportService:
    """Read-only citation projection over principal-owned canonical library metadata."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def export(
        self,
        *,
        principal_id: UUID,
        format_name: CitationExportFormat,
    ) -> CitationExport:
        async with self._session_scope() as database_session:
            records = await _citation_records(database_session, principal_id=principal_id)

        if format_name is CitationExportFormat.BIBTEX:
            return CitationExport(
                filename="bukmatika-library.bib",
                media_type="application/x-bibtex; charset=utf-8",
                content=_bibtex(records),
            )
        if format_name is CitationExportFormat.CSL_JSON:
            return CitationExport(
                filename="bukmatika-library.csl.json",
                media_type="application/vnd.citationstyles.csl+json; charset=utf-8",
                content=_csl_json(records),
            )
        return CitationExport(
            filename="bukmatika-library.ris",
            media_type="application/x-research-info-systems; charset=utf-8",
            content=_ris(records),
        )


async def _citation_records(
    session: AsyncSession,
    *,
    principal_id: UUID,
) -> list[CitationRecord]:
    rows = (
        await session.execute(
            select(LibraryEntry, Work, Edition)
            .join(Work, Work.id == LibraryEntry.work_id)
            .outerjoin(Edition, Edition.id == LibraryEntry.edition_id)
            .where(LibraryEntry.principal_id == principal_id)
            .order_by(Work.normalized_title, LibraryEntry.id)
        )
    ).all()
    if not rows:
        return []

    work_ids = {work.id for _, work, _ in rows}
    edition_ids = {edition.id for _, _, edition in rows if edition is not None}

    authors: dict[UUID, list[str]] = defaultdict(list)
    author_rows = await session.execute(
        select(WorkContributor.work_id, Contributor.display_name)
        .join(Contributor, Contributor.id == WorkContributor.contributor_id)
        .where(
            WorkContributor.work_id.in_(work_ids),
            WorkContributor.role == "author",
        )
        .order_by(WorkContributor.work_id, Contributor.normalized_name, Contributor.id)
    )
    for work_id, display_name in author_rows:
        authors[work_id].append(display_name)

    identifiers: dict[UUID, list[tuple[str, str]]] = defaultdict(list)
    if edition_ids:
        identifier_rows = await session.execute(
            select(Identifier.entity_id, Identifier.scheme, Identifier.value)
            .where(
                Identifier.entity_type == "edition",
                Identifier.entity_id.in_(edition_ids),
            )
            .order_by(Identifier.entity_id, Identifier.scheme, Identifier.normalized_value)
        )
        for entity_id, scheme, value in identifier_rows:
            identifiers[entity_id].append((scheme, value))

    source_urls = await _source_urls(
        session,
        work_ids=work_ids,
        edition_ids=edition_ids,
    )

    records: list[CitationRecord] = []
    for entry, work, edition in rows:
        edition_id = edition.id if edition is not None else None
        records.append(
            CitationRecord(
                library_entry_id=entry.id,
                work_id=work.id,
                edition_id=edition_id,
                title=edition.title if edition is not None else work.canonical_title,
                authors=tuple(authors.get(work.id, ())),
                publication_year=edition.publication_year if edition is not None else None,
                publisher=edition.publisher if edition is not None else None,
                language=edition.language if edition is not None else None,
                edition_statement=edition.edition_statement if edition is not None else None,
                isbn=_preferred_isbn(identifiers.get(edition_id, ())) if edition_id else None,
                source_url=(
                    source_urls.get(("edition", edition_id))
                    if edition_id is not None
                    else None
                )
                or source_urls.get(("work", work.id)),
            )
        )
    return records


async def _source_urls(
    session: AsyncSession,
    *,
    work_ids: set[UUID],
    edition_ids: set[UUID],
) -> dict[tuple[str, UUID], str]:
    predicates = []
    if work_ids:
        predicates.append(
            (SourceRecordLink.entity_type == "work") & SourceRecordLink.entity_id.in_(work_ids)
        )
    if edition_ids:
        predicates.append(
            (SourceRecordLink.entity_type == "edition")
            & SourceRecordLink.entity_id.in_(edition_ids)
        )
    if not predicates:
        return {}

    predicate = predicates[0]
    for extra in predicates[1:]:
        predicate = predicate | extra

    rows = await session.execute(
        select(
            SourceRecordLink.entity_type,
            SourceRecordLink.entity_id,
            SourceRecord.canonical_url,
            SourceRecord.provider,
        )
        .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
        .where(SourceRecordLink.relationship == "describes", predicate)
        .order_by(
            SourceRecordLink.entity_type,
            SourceRecordLink.entity_id,
            SourceRecord.provider,
            SourceRecord.canonical_url,
        )
    )
    result: dict[tuple[str, UUID], str] = {}
    for entity_type, entity_id, canonical_url, _provider in rows:
        result.setdefault((entity_type, entity_id), canonical_url)
    return result


def _preferred_isbn(values: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str | None:
    priorities = {
        "isbn_13": 0,
        "isbn13": 0,
        "isbn-13": 0,
        "isbn_10": 1,
        "isbn10": 1,
        "isbn-10": 1,
        "isbn": 2,
    }
    candidates = [
        (priorities[scheme.casefold()], value)
        for scheme, value in values
        if scheme.casefold() in priorities and value.strip()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def _csl_json(records: list[CitationRecord]) -> str:
    payload: list[dict[str, Any]] = []
    for record in records:
        item: dict[str, Any] = {
            "id": f"bukmatika:{record.library_entry_id}",
            "type": "book",
            "title": record.title,
        }
        if record.authors:
            item["author"] = [{"literal": author} for author in record.authors]
        if record.publication_year is not None:
            item["issued"] = {"date-parts": [[record.publication_year]]}
        if record.publisher:
            item["publisher"] = record.publisher
        if record.language:
            item["language"] = record.language
        if record.edition_statement:
            item["edition"] = record.edition_statement
        if record.isbn:
            item["ISBN"] = record.isbn
        if record.source_url:
            item["URL"] = record.source_url
        payload.append(item)
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _bibtex(records: list[CitationRecord]) -> str:
    blocks: list[str] = []
    for record in records:
        fields: list[tuple[str, str]] = [("title", record.title)]
        if record.authors:
            fields.append(("author", " and ".join(record.authors)))
        if record.publication_year is not None:
            fields.append(("year", str(record.publication_year)))
        if record.publisher:
            fields.append(("publisher", record.publisher))
        if record.edition_statement:
            fields.append(("edition", record.edition_statement))
        if record.isbn:
            fields.append(("isbn", record.isbn))
        if record.language:
            fields.append(("language", record.language))
        if record.source_url:
            fields.append(("url", record.source_url))

        key = f"bukmatika_{record.library_entry_id.hex[:12]}"
        lines = [f"@book{{{key},"]
        for index, (field, value) in enumerate(fields):
            suffix = "," if index < len(fields) - 1 else ""
            lines.append(f"  {field} = {{{_bibtex_escape(value)}}}{suffix}")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _bibtex_escape(value: str) -> str:
    escaped = value.replace("\\", r"{\textbackslash}")
    replacements = {
        "{": r"\{",
        "}": r"\}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
    }
    return "".join(replacements.get(character, character) for character in escaped)


def _ris(records: list[CitationRecord]) -> str:
    blocks: list[str] = []
    for record in records:
        lines = ["TY  - BOOK", f"ID  - bukmatika:{record.library_entry_id}"]
        lines.extend(f"AU  - {_ris_value(author)}" for author in record.authors)
        lines.append(f"TI  - {_ris_value(record.title)}")
        if record.publication_year is not None:
            lines.append(f"PY  - {record.publication_year}")
        if record.publisher:
            lines.append(f"PB  - {_ris_value(record.publisher)}")
        if record.edition_statement:
            lines.append(f"ET  - {_ris_value(record.edition_statement)}")
        if record.isbn:
            lines.append(f"SN  - {_ris_value(record.isbn)}")
        if record.language:
            lines.append(f"LA  - {_ris_value(record.language)}")
        if record.source_url:
            lines.append(f"UR  - {_ris_value(record.source_url)}")
        lines.append("ER  -")
        blocks.append("\r\n".join(lines))
    return "\r\n\r\n".join(blocks) + ("\r\n" if blocks else "")


def _ris_value(value: str) -> str:
    return re.sub(r"[\r\n]+", " ", value).strip()


__all__ = [
    "CitationExport",
    "CitationExportFormat",
    "CitationRecord",
    "LibraryCitationExportService",
]
