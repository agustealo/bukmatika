from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library import LibraryPortabilityImportPlanner
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableBookmark,
    PortableCollection,
    PortableDocumentIdentity,
    PortableEditionIdentity,
    PortableHighlight,
    PortableIdentifier,
    PortableLibraryEntry,
    PortableReadingPosition,
    PortableReadingState,
    PortableSmartShelf,
    PortableSourceReference,
    PortableTag,
    PortableWorkIdentity,
)
from bukmatika.main import app
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.library_organization_models import LibraryCollection
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    Identifier,
    LibraryEntry,
    Principal,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Work,
    WorkContributor,
)
from bukmatika.persistence.reader_models import ReadingState


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_destination(
    session: AsyncSession,
    *,
    suffix: str,
    include_document: bool = True,
    parser_version: str = "1",
) -> tuple[Principal, Work, Edition, LibraryEntry, Document | None, DocumentSection | None]:
    principal = Principal(kind="local", external_subject=f"import-{suffix}")
    work = Work(
        canonical_title=f"Portable Work {suffix}",
        normalized_title=f"portable work {suffix}",
    )
    session.add_all([principal, work])
    await session.flush()

    author = Contributor(
        display_name=f"Author {suffix}",
        normalized_name=f"author {suffix}",
    )
    session.add(author)
    await session.flush()
    session.add(WorkContributor(work_id=work.id, contributor_id=author.id, role="author"))

    edition = Edition(
        work_id=work.id,
        title=f"Portable Edition {suffix}",
        language="en",
        publication_year=1899,
        publisher="Open Press",
        edition_statement="First portable edition",
    )
    session.add(edition)
    await session.flush()
    session.add_all(
        [
            Identifier(
                entity_type="work",
                entity_id=work.id,
                scheme="lccn",
                value=f"work-{suffix}",
                normalized_value=f"work{suffix}",
            ),
            Identifier(
                entity_type="edition",
                entity_id=edition.id,
                scheme="isbn",
                value=f"978-{suffix}",
                normalized_value=f"978{suffix}",
            ),
        ]
    )
    source = SourceRecord(
        provider="fixture-provider",
        provider_record_id=f"record-{suffix}",
        canonical_url=f"https://catalog.example/{suffix}",
    )
    edition_source = SourceRecord(
        provider="fixture-provider",
        provider_record_id=f"edition-{suffix}",
        canonical_url=f"https://catalog.example/{suffix}/edition",
    )
    session.add_all([source, edition_source])
    await session.flush()
    session.add_all(
        [
            SourceRecordLink(
                source_record_id=source.id,
                entity_type="work",
                entity_id=work.id,
                relationship="describes",
            ),
            SourceRecordLink(
                source_record_id=edition_source.id,
                entity_type="edition",
                entity_id=edition.id,
                relationship="describes",
            ),
        ]
    )

    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add(entry)
    await session.flush()

    if not include_document:
        await session.flush()
        return principal, work, edition, entry, None, None

    sha = (suffix[0].lower() if suffix else "a") * 64
    stored = StoredObject(
        sha256=sha,
        storage_key=f"private/{suffix}.epub",
        byte_size=500,
        media_type="application/epub+zip",
    )
    session.add(stored)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="EPUB",
        media_type="application/epub+zip",
        stored_object_id=stored.id,
        byte_size=500,
    )
    session.add(asset)
    await session.flush()
    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=sha,
        format="EPUB",
        parser_name="epub",
        parser_version=parser_version,
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()
    section_text = "Portable chapter text for coordinate validation."
    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading="Chapter one",
        locator={"spine_index": 0, "href": "chapter.xhtml"},
        text=section_text,
    )
    session.add(section)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=0,
            char_start=0,
            char_end=len(section_text),
            text=section_text,
        )
    )
    await session.flush()
    return principal, work, edition, entry, document, section


def _manifest(
    *,
    suffix: str,
    document_sha: str,
    parser_version: str = "1",
    reading_updated_at: datetime | None = None,
) -> LibraryPortabilityExportResponse:
    now = datetime.now(UTC)
    updated_at = reading_updated_at or now
    collection_id = uuid4()
    tag_id = uuid4()
    work = PortableWorkIdentity(
        source_work_id=uuid4(),
        canonical_title=f"Portable Work {suffix}",
        authors=[f"Author {suffix}"],
        subjects=[],
        identifiers=[PortableIdentifier(scheme="lccn", value=f"WORK - {suffix}")],
        sources=[
            PortableSourceReference(
                provider="fixture-provider",
                provider_record_id=f"record-{suffix}",
                canonical_url=f"https://catalog.example/{suffix}",
                relationship="describes",
            )
        ],
    )
    edition = PortableEditionIdentity(
        source_edition_id=uuid4(),
        title=f"Portable Edition {suffix}",
        language="en",
        publication_year=1899,
        publisher="Open Press",
        edition_statement="First portable edition",
        identifiers=[PortableIdentifier(scheme="isbn", value=f"978 - {suffix}")],
        sources=[
            PortableSourceReference(
                provider="fixture-provider",
                provider_record_id=f"edition-{suffix}",
                canonical_url=f"https://catalog.example/{suffix}/edition",
                relationship="describes",
            )
        ],
    )
    locator = {"spine_index": 0, "href": "chapter.xhtml"}
    reading = PortableReadingState(
        source_reading_state_id=uuid4(),
        document=PortableDocumentIdentity(
            source_document_id=uuid4(),
            source_sha256=document_sha,
            format="EPUB",
            parser_name="epub",
            parser_version=parser_version,
        ),
        status="reading",
        progress_fraction=0.4,
        position=PortableReadingPosition(
            section_ordinal=0,
            char_offset=5,
            locator=locator,
        ),
        last_read_at=updated_at,
        created_at=updated_at - timedelta(days=2),
        updated_at=updated_at,
        bookmarks=[
            PortableBookmark(
                source_bookmark_id=uuid4(),
                section_ordinal=0,
                char_offset=5,
                locator=locator,
                label="Return here",
                created_at=updated_at,
                updated_at=updated_at,
            )
        ],
        highlights=[
            PortableHighlight(
                source_highlight_id=uuid4(),
                section_ordinal=0,
                char_start=0,
                char_end=8,
                locator=locator,
                note="Evidence",
                created_at=updated_at,
                updated_at=updated_at,
            )
        ],
    )
    return LibraryPortabilityExportResponse(
        exported_at=now,
        entries=[
            PortableLibraryEntry(
                source_library_entry_id=uuid4(),
                status="saved",
                created_at=now,
                updated_at=now,
                work=work,
                edition=edition,
                assets=[],
                reading_states=[reading],
                collection_ids=[collection_id],
                tag_ids=[tag_id],
            )
        ],
        collections=[
            PortableCollection(
                source_collection_id=collection_id,
                name="Primary Sources",
                description="Portable collection",
                created_at=now,
                updated_at=now,
            )
        ],
        tags=[
            PortableTag(
                source_tag_id=tag_id,
                name="Atlantic",
                created_at=now,
                updated_at=now,
            )
        ],
        smart_shelves=[
            PortableSmartShelf(
                source_smart_shelf_id=uuid4(),
                name="Reading Atlantic",
                description="Portable shelf",
                reading_status="reading",
                collection_id=collection_id,
                tag_id=tag_id,
                created_at=now,
                updated_at=now,
            )
        ],
    )


async def test_import_plan_resolves_durable_identity_and_ignores_source_uuids(
    session: AsyncSession,
) -> None:
    principal, work, edition, entry, document, _ = await _seed_destination(
        session,
        suffix="alpha",
    )
    assert document is not None
    local_collection = LibraryCollection(
        principal_id=principal.id,
        name="Primary Sources",
        normalized_name="primary sources",
        description="Local description wins",
    )
    session.add(local_collection)
    await session.flush()

    manifest = _manifest(suffix="alpha", document_sha=document.source_sha256)
    result = await LibraryPortabilityImportPlanner(
        session_scope_factory=_scope(session)
    ).plan(principal_id=principal.id, manifest=manifest)

    assert result.can_apply is True
    assert result.conflicts == []
    planned = result.entries[0]
    assert planned.work.action == "match"
    assert planned.work.destination_id == work.id
    assert planned.work.source_id != work.id
    assert planned.edition is not None
    assert planned.edition.action == "match"
    assert planned.edition.destination_id == edition.id
    assert planned.library_entry.action == "match"
    assert planned.library_entry.destination_id == entry.id
    assert planned.documents[0].action == "match"
    assert planned.documents[0].destination_id == document.id
    assert planned.reading_states[0].action == "apply"
    assert result.collections[0].action == "match"
    assert result.collections[0].destination_id == local_collection.id
    assert result.tags[0].action == "create"
    assert result.smart_shelves[0].action == "create"


async def test_import_plan_blocks_conflicting_strong_work_evidence(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="import-conflict")
    first = Work(canonical_title="First", normalized_title="first")
    second = Work(canonical_title="Second", normalized_title="second")
    session.add_all([principal, first, second])
    await session.flush()
    session.add_all(
        [
            Identifier(
                entity_type="work",
                entity_id=first.id,
                scheme="isbn",
                value="111",
                normalized_value="111",
            ),
            Identifier(
                entity_type="work",
                entity_id=second.id,
                scheme="lccn",
                value="222",
                normalized_value="222",
            ),
        ]
    )
    await session.flush()

    manifest = _manifest(suffix="conflict", document_sha="c" * 64)
    portable_work = manifest.entries[0].work
    portable_work.identifiers = [
        PortableIdentifier(scheme="isbn", value="111"),
        PortableIdentifier(scheme="lccn", value="222"),
    ]
    portable_work.sources = []
    manifest.entries[0].edition = None
    manifest.entries[0].reading_states = []

    result = await LibraryPortabilityImportPlanner(
        session_scope_factory=_scope(session)
    ).plan(principal_id=principal.id, manifest=manifest)

    assert result.can_apply is False
    assert result.entries[0].work.action == "conflict"
    assert any(conflict.code == "work_identity_conflict" for conflict in result.conflicts)
    assert result.entries[0].library_entry.action == "conflict"


async def test_import_plan_rejects_same_bytes_with_incompatible_parser(
    session: AsyncSession,
) -> None:
    principal, _, _, _, document, _ = await _seed_destination(
        session,
        suffix="parser",
        parser_version="1",
    )
    assert document is not None
    manifest = _manifest(
        suffix="parser",
        document_sha=document.source_sha256,
        parser_version="2",
    )

    result = await LibraryPortabilityImportPlanner(
        session_scope_factory=_scope(session)
    ).plan(principal_id=principal.id, manifest=manifest)

    assert result.can_apply is False
    assert result.entries[0].documents[0].action == "conflict"
    assert any(conflict.code == "document_parser_incompatible" for conflict in result.conflicts)


async def test_import_plan_skips_reader_state_when_local_document_is_missing(
    session: AsyncSession,
) -> None:
    principal, _, _, _, _, _ = await _seed_destination(
        session,
        suffix="missing",
        include_document=False,
    )
    manifest = _manifest(suffix="missing", document_sha="d" * 64)

    result = await LibraryPortabilityImportPlanner(
        session_scope_factory=_scope(session)
    ).plan(principal_id=principal.id, manifest=manifest)

    assert result.can_apply is True
    assert result.entries[0].documents[0].action == "skip"
    assert result.entries[0].reading_states[0].action == "skip"
    assert result.conflicts == []


async def test_import_plan_preserves_newer_local_reader_state(session: AsyncSession) -> None:
    principal, _, _, entry, document, section = await _seed_destination(
        session,
        suffix="newer",
    )
    assert document is not None
    assert section is not None
    now = datetime.now(UTC)
    local = ReadingState(
        library_entry_id=entry.id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.8,
        section_id=section.id,
        section_ordinal=0,
        char_offset=10,
        locator=section.locator,
        last_read_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(local)
    await session.flush()
    manifest = _manifest(
        suffix="newer",
        document_sha=document.source_sha256,
        reading_updated_at=now - timedelta(days=1),
    )

    result = await LibraryPortabilityImportPlanner(
        session_scope_factory=_scope(session)
    ).plan(principal_id=principal.id, manifest=manifest)

    assert result.can_apply is False
    assert result.entries[0].reading_states[0].action == "conflict"
    assert any(conflict.code == "local_reader_state_newer" for conflict in result.conflicts)


def test_library_import_plan_route_is_mounted() -> None:
    assert "/v1/library/import/plan" in set(app.openapi()["paths"])
