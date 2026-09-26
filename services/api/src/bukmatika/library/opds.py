from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import UUID
from xml.etree import ElementTree

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.domain import LibraryItemResponse
from bukmatika.library.service import LibraryService
from bukmatika.persistence import session_scope
from bukmatika.persistence.library import LibraryRepository

ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
OPDS_MEDIA_TYPE = "application/atom+xml;profile=opds-catalog;kind=navigation"
SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

ElementTree.register_namespace("", ATOM_NAMESPACE)


class OpdsCatalogService:
    """Principal-scoped OPDS navigation projection over the canonical Library authority."""

    def __init__(
        self,
        *,
        library_service: LibraryService | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._library_service = library_service or LibraryService(
            session_scope_factory=session_scope_factory
        )
        self._session_scope = session_scope_factory

    async def render(
        self,
        *,
        principal_id: UUID,
        self_url: str,
        web_origin: str,
    ) -> bytes:
        library = await self._library_service.list_library(principal_id=principal_id)
        async with self._session_scope() as database_session:
            entries = await LibraryRepository(database_session).library_entries(principal_id)
        updated_by_entry = {entry.id: entry.updated_at for entry in entries}
        return render_opds_feed(
            principal_id=principal_id,
            items=library.items,
            updated_by_entry=updated_by_entry,
            self_url=self_url,
            web_origin=web_origin,
            generated_at=datetime.now(UTC),
        )


def render_opds_feed(
    *,
    principal_id: UUID,
    items: list[LibraryItemResponse],
    updated_by_entry: Mapping[UUID, datetime],
    self_url: str,
    web_origin: str,
    generated_at: datetime,
) -> bytes:
    """Render a deterministic OPDS navigation feed without copying catalog state."""

    root = ElementTree.Element(_atom("feed"))
    _text(root, "id", f"urn:bukmatika:library:{principal_id}")
    _text(root, "title", "Bukmatika Personal Library")
    _text(root, "updated", _rfc3339(_feed_updated(items, updated_by_entry, generated_at)))
    feed_author = ElementTree.SubElement(root, _atom("author"))
    _text(feed_author, "name", "Bukmatika")
    ElementTree.SubElement(
        root,
        _atom("link"),
        {"rel": "self", "href": self_url, "type": OPDS_MEDIA_TYPE},
    )
    ElementTree.SubElement(
        root,
        _atom("link"),
        {"rel": "start", "href": self_url, "type": OPDS_MEDIA_TYPE},
    )
    ElementTree.SubElement(
        root,
        _atom("link"),
        {
            "rel": "alternate",
            "href": f"{web_origin.rstrip('/')}/library",
            "type": "text/html",
        },
    )

    for item in items:
        entry = ElementTree.SubElement(root, _atom("entry"))
        _text(entry, "id", f"urn:bukmatika:library-entry:{item.library_entry_id}")
        _text(entry, "title", item.title)
        _text(
            entry,
            "updated",
            _rfc3339(updated_by_entry.get(item.library_entry_id, generated_at)),
        )
        for author in item.authors:
            author_element = ElementTree.SubElement(entry, _atom("author"))
            _text(author_element, "name", author)

        reading_status = item.reading_status or "unread"
        ElementTree.SubElement(
            entry,
            _atom("category"),
            {
                "scheme": "urn:bukmatika:reading-status",
                "term": reading_status,
                "label": f"Reading status: {reading_status}",
            },
        )
        ElementTree.SubElement(
            entry,
            _atom("category"),
            {
                "scheme": "urn:bukmatika:library-status",
                "term": item.status,
                "label": f"Library status: {item.status}",
            },
        )

        dossier_href = _dossier_href(web_origin, item.work_id)
        if item.readable_document_id is not None:
            primary_href = _reader_href(
                web_origin,
                item.library_entry_id,
                item.readable_document_id,
            )
            ElementTree.SubElement(
                entry,
                _atom("link"),
                {"rel": "alternate", "href": primary_href, "type": "text/html"},
            )
            ElementTree.SubElement(
                entry,
                _atom("link"),
                {"rel": "related", "href": dossier_href, "type": "text/html"},
            )
        else:
            ElementTree.SubElement(
                entry,
                _atom("link"),
                {"rel": "alternate", "href": dossier_href, "type": "text/html"},
            )

        summary = _summary(item)
        _text(entry, "content", summary, attributes={"type": "text"})

    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _feed_updated(
    items: list[LibraryItemResponse],
    updated_by_entry: Mapping[UUID, datetime],
    generated_at: datetime,
) -> datetime:
    values = [
        updated_by_entry[item.library_entry_id]
        for item in items
        if item.library_entry_id in updated_by_entry
    ]
    return max(values) if values else generated_at


def _summary(item: LibraryItemResponse) -> str:
    parts = [f"Saved in Bukmatika ({item.status})."]
    if item.readable_format:
        parts.append(f"Readable format: {item.readable_format}.")
    if item.progress_fraction is not None:
        parts.append(f"Reading progress: {round(item.progress_fraction * 100)}%.")
    return " ".join(parts)


def _reader_href(web_origin: str, library_entry_id: UUID, document_id: UUID) -> str:
    return f"{web_origin.rstrip('/')}/read/{library_entry_id}/{document_id}"


def _dossier_href(web_origin: str, work_id: UUID) -> str:
    return f"{web_origin.rstrip('/')}/dossier?{urlencode({'work_id': str(work_id)})}"


def _atom(name: str) -> str:
    return f"{{{ATOM_NAMESPACE}}}{name}"


def _text(
    parent: ElementTree.Element,
    name: str,
    value: str,
    *,
    attributes: dict[str, str] | None = None,
) -> ElementTree.Element:
    child = ElementTree.SubElement(parent, _atom(name), attributes or {})
    child.text = value
    return child


def _rfc3339(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


__all__ = ["ATOM_NAMESPACE", "OPDS_MEDIA_TYPE", "OpdsCatalogService", "render_opds_feed"]
