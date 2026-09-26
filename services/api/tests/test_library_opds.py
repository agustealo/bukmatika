from datetime import UTC, datetime, timedelta
from uuid import uuid4
from xml.etree import ElementTree

from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.library.domain import LibraryItemResponse
from bukmatika.library.opds import ATOM_NAMESPACE, OPDS_MEDIA_TYPE, render_opds_feed
from bukmatika.library.opds_routes import personal_library_opds

ATOM = f"{{{ATOM_NAMESPACE}}}"


def _item(
    *,
    title: str,
    authors: list[str],
    readable: bool,
    progress: float | None = None,
) -> LibraryItemResponse:
    return LibraryItemResponse(
        library_entry_id=uuid4(),
        work_id=uuid4(),
        edition_id=uuid4(),
        title=title,
        authors=authors,
        status="saved",
        readable_document_id=uuid4() if readable else None,
        readable_format="EPUB" if readable else None,
        progress_fraction=progress,
        reading_status="reading" if progress is not None else None,
        collections=[],
        tags=[],
    )


def test_opds_feed_projects_canonical_library_metadata_and_reader_links() -> None:
    principal_id = uuid4()
    readable = _item(
        title="History & <Memory>",
        authors=["Ada & Byron", "Grace Hopper"],
        readable=True,
        progress=0.42,
    )
    saved = _item(title="Unprocessed Book", authors=["Archive Author"], readable=False)
    older = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    newer = datetime(2026, 9, 26, 12, 30, tzinfo=UTC)

    payload = render_opds_feed(
        principal_id=principal_id,
        items=[readable, saved],
        updated_by_entry={
            readable.library_entry_id: older,
            saved.library_entry_id: newer,
        },
        self_url="/v1/opds/library",
        web_origin="http://web.local",
        generated_at=datetime(2026, 9, 26, 13, 0, tzinfo=UTC),
    )
    root = ElementTree.fromstring(payload)

    assert root.tag == f"{ATOM}feed"
    assert root.findtext(f"{ATOM}id") == f"urn:bukmatika:library:{principal_id}"
    assert root.findtext(f"{ATOM}updated") == "2026-09-26T12:30:00Z"
    assert root.findtext(f"{ATOM}title") == "Bukmatika Personal Library"

    feed_links = {
        element.attrib["rel"]: element.attrib
        for element in root.findall(f"{ATOM}link")
    }
    assert feed_links["self"] == {
        "rel": "self",
        "href": "/v1/opds/library",
        "type": OPDS_MEDIA_TYPE,
    }
    assert feed_links["alternate"]["href"] == "http://web.local/library"

    entries = root.findall(f"{ATOM}entry")
    assert len(entries) == 2
    first = entries[0]
    assert first.findtext(f"{ATOM}title") == "History & <Memory>"
    assert [
        author.findtext(f"{ATOM}name") for author in first.findall(f"{ATOM}author")
    ] == ["Ada & Byron", "Grace Hopper"]
    assert first.findtext(f"{ATOM}content") == (
        "Saved in Bukmatika (saved). Readable format: EPUB. Reading progress: 42%."
    )
    first_links = {
        element.attrib["rel"]: element.attrib["href"]
        for element in first.findall(f"{ATOM}link")
    }
    assert first_links["alternate"] == (
        f"http://web.local/read/{readable.library_entry_id}/{readable.readable_document_id}"
    )
    assert first_links["related"] == (
        f"http://web.local/dossier?work_id={readable.work_id}"
    )

    second = entries[1]
    second_links = second.findall(f"{ATOM}link")
    assert len(second_links) == 1
    assert second_links[0].attrib == {
        "rel": "alternate",
        "href": f"http://web.local/dossier?work_id={saved.work_id}",
        "type": "text/html",
    }


def test_opds_empty_library_uses_generation_time_and_contains_no_entries() -> None:
    generated = datetime(2026, 9, 26, 14, 5, 6, 987654, tzinfo=UTC)
    payload = render_opds_feed(
        principal_id=uuid4(),
        items=[],
        updated_by_entry={},
        self_url="/v1/opds/library",
        web_origin="http://web.local/",
        generated_at=generated,
    )
    root = ElementTree.fromstring(payload)

    assert root.findtext(f"{ATOM}updated") == "2026-09-26T14:05:06Z"
    assert root.findall(f"{ATOM}entry") == []
    alternate = next(
        element for element in root.findall(f"{ATOM}link") if element.attrib["rel"] == "alternate"
    )
    assert alternate.attrib["href"] == "http://web.local/library"


async def test_opds_route_is_private_and_passes_principal_scope_to_service() -> None:
    principal_id = uuid4()
    identity = AuthenticatedPrincipal(
        principal_id=principal_id,
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class StubOpdsService:
        principal: object | None = None
        self_url: str | None = None
        web_origin: str | None = None

        async def render(self, *, principal_id: object, self_url: str, web_origin: str) -> bytes:
            self.principal = principal_id
            self.self_url = self_url
            self.web_origin = web_origin
            return b'<?xml version="1.0"?><feed />'

    service = StubOpdsService()
    response = await personal_library_opds(
        identity=identity,
        service=service,  # type: ignore[arg-type]
    )

    assert service.principal == principal_id
    assert service.self_url == "/v1/opds/library"
    assert response.status_code == 200
    assert response.media_type == OPDS_MEDIA_TYPE
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["vary"] == "Cookie"
    assert response.headers["content-disposition"] == 'inline; filename="bukmatika-library.xml"'
