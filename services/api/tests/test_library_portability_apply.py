from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.portability_apply import LibraryPortabilityImportApplier
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
from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.library_organization_models import (
    LibraryCollection,
    LibrarySmartShelf,
    LibraryTag,
)
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
from bukmatika.persistence.reader_models import Bookmark, Highlight, ReadingState


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


async def _seed_destination(
    session: AsyncSession,
    *,
    suffix: str,
) -> tuple[Principal, LibraryEntry, Document, DocumentSection]:
    principal = Principal(kind="local", external_subject=f"apply-{suffix}")
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
    work_source = SourceRecord(
        provider="fixture-provider",
        provider_record_id=f"record-{suffix}",
        canonical_url=f"https://catalog.example/{suffix}",
    )
    edition_source = SourceRecord(
        provider="fixture-provider",
        provider_record_id=f"edition-{suffix}",
        canonical_url=f"https://catalog.example/{suffix}/edition",
    )
    session.add_all([work_source, edition_source])
    await session.flush()
    session.add_all(
        [
            SourceRecordLink(
                source_record_id=work_source.id,
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
        parser_version="1",
        section_count=1,
        chunk_count=0,
    )
    session.add(document)
    await session.flush()
    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading="Chapter one",
        locator={"spine_index": 0, "href": "chapter.xhtml"},
        text="Portable chapter text for coordinate validation.",
    )
    session.add(section)
    await session.flush()
    return principal, entry, document, section


def _manifest(
    *,
    suffix: str,
    document_sha: str,
    reading_updated_at: datetime,
) -> LibraryPortabilityExportResponse:
    collection_id = uuid4()
    tag_id = uuid4()
    locator = {"spine_index": 0, "href": "chapter.xhtml"}
    work = PortableWorkIdentity(
        source_work_id=uuid4(),
        canonical_title=f"Portable Work {suffix}",
        authors=[f"Author {suffix}"],
        subjects=["History"],
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
    reading = PortableReadingState(
        source_reading_state_id=uuid4(),
        document=PortableDocumentIdentity(
            source_document_id=uuid4(),
            source_sha256=document_sha,
            format="EPUB",
            parser_name="epub",
            parser_version="1",
        ),
        status="reading",
        progress_fraction=0.4,
        position=PortableReadingPosition(
            section_ordinal=0,
            char_offset=5,
            locator=locator,
        ),
        last_read_at=reading_updated_at,
        created_at=reading_updated_at - timedelta(days=2),
        updated_at=reading_updated_at,
        bookmarks=[
            PortableBookmark(
                source_bookmark_id=uuid4(),
                section_ordinal=0,
                char_offset=5,
                locator=locator,
                label="Return here",
                created_at=reading_updated_at,
                updated_at=reading_updated_at,
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
                created_at=reading_updated_at,
                updated_at=reading_updated_at,
            )
        ],
    )
    return LibraryPortabilityExportResponse(
        exported_at=reading_updated_at,
        entries=[
            PortableLibraryEntry(
                source_library_entry_id=uuid4(),
                status="saved",
                created_at=reading_updated_at,
                updated_at=reading_updated_at,
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
                created_at=reading_updated_at,
                updated_at=reading_updated_at,
            )
        ],
        tags=[
            PortableTag(
                source_tag_id=tag_id,
                name="Atlantic",
                created_at=reading_updated_at,
                updated_at=reading_updated_at,
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
                created_at=reading_updated_at,
                updated_at=reading_updated_at,
            )
        ],
    )


async def test_import_apply_is_transactional_and_idempotent(session: AsyncSession) -> None:
    principal, entry, document, _ = await _seed_destination(session, suffix="apply")
    now = datetime.now(UTC)
    manifest = _manifest(
        suffix="apply",
        document_sha=document.source_sha256,
        reading_updated_at=now,
    )
    applier = LibraryPortabilityImportApplier(session_scope_factory=_scope(session))

    first = await applier.apply(principal_id=principal.id, manifest=manifest)

    assert first.committed is True
    assert first.plan.can_apply is True
    assert first.summary.collections_created == 1
    assert first.summary.tags_created == 1
    assert first.summary.smart_shelves_created == 1
    assert first.summary.reading_states_applied == 1
    assert first.summary.library_entries_created == 0

    state = await session.scalar(
        select(ReadingState).where(
            ReadingState.library_entry_id == entry.id,
            ReadingState.document_id == document.id,
        )
    )
    assert state is not None
    assert state.progress_fraction == 0.4
    assert state.updated_at == now
    assert await session.scalar(
        select(func.count()).select_from(Bookmark).where(Bookmark.reading_state_id == state.id)
    ) == 1
    assert await session.scalar(
        select(func.count()).select_from(Highlight).where(Highlight.reading_state_id == state.id)
    ) == 1

    second = await applier.apply(principal_id=principal.id, manifest=manifest)

    assert second.committed is True
    assert second.summary.collections_created == 0
    assert second.summary.tags_created == 0
    assert second.summary.smart_shelves_created == 0
    assert second.summary.reading_states_applied == 1
    assert await session.scalar(select(func.count()).select_from(LibraryCollection)) == 1
    assert await session.scalar(select(func.count()).select_from(LibraryTag)) == 1
    assert await session.scalar(select(func.count()).select_from(LibrarySmartShelf)) == 1
    assert await session.scalar(select(func.count()).select_from(Bookmark)) == 1
    assert await session.scalar(select(func.count()).select_from(Highlight)) == 1


async def test_import_apply_rolls_back_when_local_bookmark_is_newer(
    session: AsyncSession,
) -> None:
    principal, entry, document, section = await _seed_destination(session, suffix="rollback")
    imported_at = datetime.now(UTC) - timedelta(hours=1)
    manifest = _manifest(
        suffix="rollback",
        document_sha=document.source_sha256,
        reading_updated_at=imported_at,
    )
    local_state = ReadingState(
        library_entry_id=entry.id,
        document_id=document.id,
        status="reading",
        progress_fraction=0.2,
        section_id=section.id,
        section_ordinal=section.ordinal,
        char_offset=5,
        locator=section.locator,
        last_read_at=imported_at - timedelta(days=1),
        created_at=imported_at - timedelta(days=2),
        updated_at=imported_at - timedelta(days=1),
    )
    session.add(local_state)
    await session.flush()
    local_bookmark = Bookmark(
        reading_state_id=local_state.id,
        section_id=section.id,
        char_offset=5,
        locator=section.locator,
        label="Keep local edit",
        created_at=imported_at,
        updated_at=imported_at + timedelta(minutes=30),
    )
    session.add(local_bookmark)
    await session.flush()

    result = await LibraryPortabilityImportApplier(
        session_scope_factory=_scope(session)
    ).apply(principal_id=principal.id, manifest=manifest)

    assert result.committed is False
    assert result.plan.can_apply is False
    assert any(conflict.code == "local_bookmark_newer" for conflict in result.plan.conflicts)
    await session.refresh(local_state)
    await session.refresh(local_bookmark)
    assert local_state.progress_fraction == 0.2
    assert local_bookmark.label == "Keep local edit"
    assert await session.scalar(select(func.count()).select_from(LibraryCollection)) == 0
    assert await session.scalar(select(func.count()).select_from(LibraryTag)) == 0
    assert await session.scalar(select(func.count()).select_from(LibrarySmartShelf)) == 0


def test_library_import_apply_route_is_mounted() -> None:
    assert "/v1/library/import/apply" in set(app.openapi()["paths"])
