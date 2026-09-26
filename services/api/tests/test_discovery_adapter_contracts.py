from collections.abc import Callable

import httpx
import pytest

from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveryAdapter
from bukmatika.discovery.gutenberg import ProjectGutenbergAdapter
from bukmatika.discovery.internet_archive import InternetArchiveAdapter
from bukmatika.discovery.library_of_congress import LibraryOfCongressAdapter
from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.domain import SearchIntent

ProviderHandler = Callable[[httpx.Request], httpx.Response]

PROVIDERS = (
    "open_library",
    "internet_archive",
    "project_gutenberg",
    "library_of_congress",
)


def _adapter(name: str, handler: ProviderHandler) -> tuple[httpx.AsyncClient, DiscoveryAdapter]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(contact_email="contract@example.test")
    if name == "open_library":
        return client, OpenLibraryAdapter(client, settings)
    if name == "internet_archive":
        return client, InternetArchiveAdapter(client, settings)
    if name == "project_gutenberg":
        return client, ProjectGutenbergAdapter(client, settings)
    if name == "library_of_congress":
        return client, LibraryOfCongressAdapter(client, settings)
    raise AssertionError(f"Unknown provider contract fixture: {name}")


def _success_handler(name: str) -> ProviderHandler:
    if name == "open_library":
        def open_library(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "openlibrary.org"
            assert request.url.path == "/search.json"
            assert request.headers["accept"] == "application/json"
            assert "contract@example.test" in request.headers["user-agent"]
            return httpx.Response(
                200,
                request=request,
                json={
                    "docs": [
                        {
                            "key": "/works/OL123W",
                            "title": "Contract Book",
                            "author_name": ["Ada Contract"],
                            "first_publish_year": 1899,
                            "language": ["eng"],
                            "subject": ["Testing"],
                            "edition_key": ["OL123M"],
                            "ebook_access": "public",
                            "public_scan_b": True,
                        }
                    ]
                },
            )

        return open_library

    if name == "internet_archive":
        def internet_archive(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "archive.org"
            if request.url.path == "/advancedsearch.php":
                assert request.url.params["rows"] == "2"
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "response": {
                            "docs": [
                                {
                                    "identifier": "contract-book",
                                    "title": "Contract Book",
                                    "creator": ["Ada Contract"],
                                    "year": 1899,
                                }
                            ]
                        }
                    },
                )
            if request.url.path == "/metadata/contract-book":
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "metadata": {
                            "identifier": "contract-book",
                            "title": "Contract Book",
                            "creator": "Ada Contract",
                            "date": "1899",
                            "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
                        },
                        "files": [
                            {
                                "name": "contract-book.pdf",
                                "format": "Text PDF",
                                "source": "original",
                                "size": "1234",
                                "sha1": "abc123",
                            }
                        ],
                    },
                )
            raise AssertionError(f"Unexpected Internet Archive URL: {request.url}")

        return internet_archive

    if name == "project_gutenberg":
        def project_gutenberg(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "www.gutenberg.org"
            assert request.url.path == "/ebooks/search.opds/"
            assert "opds-catalog" in request.headers["accept"]
            return httpx.Response(
                200,
                request=request,
                content=b'''<?xml version="1.0" encoding="utf-8"?>
                <feed xmlns="http://www.w3.org/2005/Atom">
                  <entry>
                    <id>https://www.gutenberg.org/ebooks/12345</id>
                    <title>Contract Book</title>
                    <author><name>Ada Contract</name></author>
                    <category term="en" scheme="http://purl.org/dc/terms/language" />
                    <link rel="http://opds-spec.org/acquisition/open-access"
                          type="application/epub+zip"
                          href="https://www.gutenberg.org/ebooks/12345.epub3.images" />
                  </entry>
                </feed>''',
            )

        return project_gutenberg

    if name == "library_of_congress":
        def library_of_congress(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "www.loc.gov"
            if request.url.path == "/books/":
                assert request.url.params["c"] == "2"
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "results": [
                            {
                                "id": "https://www.loc.gov/item/contract-loc/",
                                "title": "Contract Book",
                                "contributor": ["Ada Contract"],
                                "date": "1899",
                                "digitized": True,
                            }
                        ]
                    },
                )
            if request.url.path == "/item/contract-loc/":
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "item": {
                            "id": "https://www.loc.gov/item/contract-loc/",
                            "title": "Contract Book",
                            "contributor_names": ["Ada Contract"],
                            "date_issued": "1899",
                            "access_restricted": False,
                            "rights": ["Rights status requires research"],
                        },
                        "resources": [
                            {
                                "pdf": (
                                    "https://tile.loc.gov/storage-services/service/"
                                    "contract-loc/book.pdf"
                                )
                            }
                        ],
                    },
                )
            raise AssertionError(f"Unexpected Library of Congress URL: {request.url}")

        return library_of_congress

    raise AssertionError(f"Unknown provider contract fixture: {name}")


def _empty_handler(name: str) -> ProviderHandler:
    if name == "open_library":
        return lambda request: httpx.Response(200, request=request, json={"docs": []})
    if name == "internet_archive":
        return lambda request: httpx.Response(
            200,
            request=request,
            json={"response": {"docs": []}},
        )
    if name == "project_gutenberg":
        return lambda request: httpx.Response(
            200,
            request=request,
            content=(
                b'<?xml version="1.0" encoding="utf-8"?>'
                b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            ),
        )
    if name == "library_of_congress":
        return lambda request: httpx.Response(200, request=request, json={"results": []})
    raise AssertionError(f"Unknown provider contract fixture: {name}")


@pytest.mark.parametrize("provider_name", PROVIDERS)
async def test_discovery_adapter_success_contract(provider_name: str) -> None:
    client, adapter = _adapter(provider_name, _success_handler(provider_name))
    try:
        records = await adapter.search(SearchIntent(query="contract history", limit=2))
    finally:
        await client.aclose()

    assert adapter.name == provider_name
    assert len(records) == 1
    record = records[0]
    candidate = record.candidate

    assert record.parser_version.strip()
    assert isinstance(record.source_payload, dict)
    assert record.source_payload
    assert candidate.source == provider_name
    assert candidate.source_record_id.strip()
    assert candidate.work_key.strip()
    assert candidate.title == "Contract Book"
    assert candidate.authors == ["Ada Contract"]
    assert str(candidate.landing_url).startswith("https://")
    assert candidate.rights
    assert all(evidence.source == provider_name for evidence in candidate.rights)
    for asset in candidate.assets:
        assert asset.rights == candidate.rights


@pytest.mark.parametrize("provider_name", PROVIDERS)
async def test_discovery_adapter_valid_empty_response_contract(provider_name: str) -> None:
    client, adapter = _adapter(provider_name, _empty_handler(provider_name))
    try:
        records = await adapter.search(SearchIntent(query="contract history", limit=2))
    finally:
        await client.aclose()

    assert records == []


@pytest.mark.parametrize("provider_name", PROVIDERS)
async def test_discovery_adapter_primary_http_failure_propagates(provider_name: str) -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, text="provider unavailable")

    client, adapter = _adapter(provider_name, unavailable)
    try:
        with pytest.raises(httpx.HTTPStatusError) as failure:
            await adapter.search(SearchIntent(query="contract history", limit=2))
    finally:
        await client.aclose()

    assert failure.value.response.status_code == 503
