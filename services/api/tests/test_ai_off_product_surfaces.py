from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import DiscoveryCandidate, SearchIntent
from bukmatika.library.service import LibraryService
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.personalization.domain import PersonalizationSettingsUpdate
from bukmatika.personalization.service import PersonalizationService
from bukmatika.reader.service import ReaderService
from bukmatika.research.domain import ResearchSearchRequest
from bukmatika.research.service import ResearchService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _StaticDiscoveryAdapter:
    name = "ai-off-proof"

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        return [
            DiscoveredRecord(
                candidate=DiscoveryCandidate(
                    source=self.name,
                    source_record_id="proof-1",
                    work_key="proof:work:1",
                    title="A Non-AI Discovery Result",
                    landing_url="https://example.org/proof",
                    source_score=0.8,
                ),
                source_payload={"query": intent.query},
                parser_version="1",
            )
        ]


async def _seed_readable_book(
    session: AsyncSession,
    *,
    principal: Principal,
) -> tuple[LibraryEntry, Document]:
    token = uuid4().hex
    work = Work(
        canonical_title="AI Off Research Book",
        normalized_title="ai off research book",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/ai-off/{token}",
        byte_size=128,
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()
    edition = Edition(
        work_id=work.id,
        title="AI Off Research Book",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url="https://example.org/ai-off.txt",
        stored_object_id=stored.id,
        byte_size=128,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()
    text = "Maritime navigation evidence remains searchable while AI assistance is disabled."
    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading="Navigation",
        locator={"page": 1},
        text=text,
    )
    session.add(section)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=0,
            char_start=0,
            char_end=len(text),
            text=text,
        )
    )
    await session.flush()
    return entry, document


async def test_ai_off_keeps_discovery_library_reader_and_research_operational(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="ai-off-consumer-proof")
    session.add(principal)
    await session.flush()
    scope = _scope(session)
    await PersonalizationService(session_scope_factory=scope).update_settings(
        principal_id=principal.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=True,
            autonomy_level=0,
        ),
    )
    entry, document = await _seed_readable_book(session, principal=principal)

    registry = ProviderRegistry(
        [
            ProviderRegistration(
                adapter=_StaticDiscoveryAdapter(),
                max_results=10,
                timeout_seconds=1.0,
            )
        ]
    )
    discovery = DiscoveryService(
        registry,
        session_timeout_seconds=2.0,
        max_records=10,
    )
    discovered = await discovery.discover(
        SearchIntent(query="navigation", limit=5),
        discovery.create_session(),
    )
    assert [candidate.title for candidate in discovered.response.candidates] == [
        "A Non-AI Discovery Result"
    ]

    library = await LibraryService(session_scope_factory=scope).list_library(
        principal_id=principal.id
    )
    assert len(library.items) == 1
    assert library.items[0].library_entry_id == entry.id

    reader = await ReaderService(session_scope_factory=scope).open_reader(
        principal_id=principal.id,
        library_entry_id=entry.id,
        document_id=document.id,
        after_ordinal=None,
        limit=20,
    )
    assert reader.document_id == document.id
    assert len(reader.sections) == 1
    assert "Maritime navigation evidence" in reader.sections[0].text

    research = await ResearchService(session_scope_factory=scope).search(
        principal_id=principal.id,
        request=ResearchSearchRequest(
            query="navigation evidence",
            library_entry_ids=[entry.id],
            limit=10,
        ),
    )
    assert research.selected_library_entry_ids == [entry.id]
    assert len(research.passages) == 1
    assert research.passages[0].document_id == document.id
