from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import AcquisitionStatus
from bukmatika.acquisition.downloader import SafeDownloader
from bukmatika.acquisition.network import PinnedTarget
from bukmatika.acquisition.service import AcquisitionService
from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.catalog import CatalogResolver
from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import (
    DiscoveredAsset,
    DiscoveryCandidate,
    RightsEvidence,
    RightsState,
    SearchIntent,
)
from bukmatika.library import LibraryService
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Asset, Principal
from bukmatika.processing import DocumentProcessingService, ParserRegistry, TextDocumentParser
from bukmatika.reader import ReaderService
from bukmatika.reader.domain import ReadingProgressUpdate
from bukmatika.research import ResearchService
from bukmatika.research.domain import ResearchSearchRequest

BOOK_TEXT = (
    "Mariners mapped obsidian navigation routes across the old world.\n\n"
    "A second passage records agricultural exchange and navigation evidence."
)
REMOTE_URL = "https://files.example.org/journey/book.txt"


class JourneyProvider:
    name = "journey-provider"

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        evidence = RightsEvidence(
            state=RightsState.OPEN_LICENSE,
            source=self.name,
            basis="Fixture provider grants an open license for the exact text asset.",
        )
        return [
            DiscoveredRecord(
                candidate=DiscoveryCandidate(
                    source=self.name,
                    source_record_id="journey-edition-1",
                    record_kind="edition",
                    work_key="journey-provider:journey-work-1",
                    identifiers={"isbn": ["9780000000099"]},
                    title="Journey Through the Old World",
                    authors=["Fixture Author"],
                    landing_url=HttpUrl("https://catalog.example.org/journey-edition-1"),
                    formats=["TXT"],
                    assets=[
                        DiscoveredAsset(
                            name="book.txt",
                            url=HttpUrl(REMOTE_URL),
                            format="TXT",
                            media_type="text/plain",
                            rights=[evidence],
                        )
                    ],
                    rights=[evidence],
                ),
                source_payload={"id": "journey-edition-1"},
                parser_version="journey-v1",
            )
        ]


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _pinned(url: str) -> PinnedTarget:
    return PinnedTarget(
        original_url=url,
        request_url="https://93.184.216.34/journey/book.txt",
        host_header="files.example.org",
        sni_hostname="files.example.org",
        resolved_ip="93.184.216.34",
    )


async def test_consumer_spine_reaches_reader_and_research_from_discovery(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = Principal(kind="local", external_subject="consumer-spine-journey")
    session.add(principal)
    await session.flush()

    discovery = DiscoveryService(
        ProviderRegistry(
            [ProviderRegistration(JourneyProvider(), max_results=5, timeout_seconds=1)]
        ),
        session_timeout_seconds=1,
        max_records=5,
    )
    batch = await discovery.discover(
        SearchIntent(query="old world navigation", limit=5),
        discovery.create_session(),
    )
    assert len(batch.records) == 1
    assert batch.response.candidates[0].title == "Journey Through the Old World"

    await CatalogResolver(CatalogRepository(session)).ingest(batch.records[0])
    await session.flush()
    asset = await session.scalar(select(Asset).where(Asset.remote_url == REMOTE_URL))
    assert asset is not None

    library = LibraryService(session_scope_factory=_scope(session))
    saved = await library.save_edition(
        principal_id=principal.id,
        edition_id=asset.edition_id,
    )

    request_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            content=BOOK_TEXT.encode("utf-8"),
            request=request,
        )

    async def resolver(url: str) -> PinnedTarget:
        return _pinned(url)

    store = LocalObjectStore(tmp_path)
    settings = Settings(
        storage_root=tmp_path,
        acquisition_worker_enabled=False,
        processing_max_bytes=1_048_576,
        processing_timeout_seconds=5,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        acquisition = await AcquisitionService(
            SafeDownloader(
                client,
                max_bytes=1_048_576,
                redirect_limit=1,
                chunk_size=1024,
                timeout_seconds=5,
                user_agent="Bukmatika-Journey-Test",
                resolver=resolver,
            ),
            store,
            settings,
            session_scope_factory=_scope(session),
        ).acquire(asset.id)

    assert acquisition.status is AcquisitionStatus.STORED
    assert request_count == 1

    processed = await DocumentProcessingService(
        ParserRegistry((TextDocumentParser(),)),
        store,
        settings,
        session_scope_factory=_scope(session),
    ).process_asset(asset.id)

    library_view = await library.list_library(principal_id=principal.id)
    assert len(library_view.items) == 1
    library_item = library_view.items[0]
    assert library_item.library_entry_id == saved.library_entry_id
    assert library_item.readable_document_id == processed.document_id
    assert library_item.readable_format == "TXT"

    reader = ReaderService(session_scope_factory=_scope(session))
    opened = await reader.open_reader(
        principal_id=principal.id,
        library_entry_id=saved.library_entry_id,
        document_id=processed.document_id,
        after_ordinal=None,
        limit=12,
    )
    assert opened.sections
    first_section = opened.sections[0]
    assert "obsidian navigation" in first_section.text

    progress = await reader.save_progress(
        principal_id=principal.id,
        library_entry_id=saved.library_entry_id,
        document_id=processed.document_id,
        update=ReadingProgressUpdate(
            section_id=first_section.section_id,
            char_offset=10,
        ),
    )
    assert progress.status == "reading"
    assert 0 < progress.progress_fraction < 1

    research = await ResearchService(session_scope_factory=_scope(session)).search(
        principal_id=principal.id,
        request=ResearchSearchRequest(
            query="obsidian navigation",
            library_entry_ids=[saved.library_entry_id],
            limit=5,
        ),
    )
    assert research.passages
    passage = research.passages[0]
    assert passage.library_entry_id == saved.library_entry_id
    assert passage.asset_id == asset.id
    assert passage.document_id == processed.document_id
    assert passage.section_id == first_section.section_id
    assert passage.section_ordinal == first_section.ordinal
    assert first_section.text[passage.char_start : passage.char_end] == passage.text
