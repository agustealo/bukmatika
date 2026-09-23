from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.portability_apply import LibraryPortabilityImportApplier
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableAssetManifest,
    PortableBookmark,
    PortableBytePolicy,
    PortableCollection,
    PortableDocumentIdentity,
    PortableEditionIdentity,
    PortableIdentifier,
    PortableLibraryEntry,
    PortableReadingState,
    PortableWorkIdentity,
)
from bukmatika.library.portability_import import LibraryPortabilityImportPlanner
from bukmatika.library.portability_validation import validate_manifest_consistency
from bukmatika.persistence.models import Principal, Work


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


def _work(
    *,
    source_id: UUID | None = None,
    title: str = "Portable Work",
    lccn: str | None = None,
) -> PortableWorkIdentity:
    return PortableWorkIdentity(
        source_work_id=source_id or uuid4(),
        canonical_title=title,
        authors=["Ada Researcher"],
        subjects=["History"],
        identifiers=(
            [PortableIdentifier(scheme="lccn", value=lccn)] if lccn is not None else []
        ),
        sources=[],
    )


def _edition(
    *,
    source_id: UUID | None = None,
    title: str = "Portable Edition",
    isbn: str | None = None,
) -> PortableEditionIdentity:
    return PortableEditionIdentity(
        source_edition_id=source_id or uuid4(),
        title=title,
        language="en",
        publication_year=1901,
        publisher="Open Press",
        edition_statement="First edition",
        identifiers=(
            [PortableIdentifier(scheme="isbn", value=isbn)] if isbn is not None else []
        ),
        sources=[],
    )


def _entry(
    *,
    work: PortableWorkIdentity,
    edition: PortableEditionIdentity | None = None,
    entry_id: UUID | None = None,
    assets: list[PortableAssetManifest] | None = None,
    readings: list[PortableReadingState] | None = None,
) -> PortableLibraryEntry:
    now = datetime.now(UTC)
    return PortableLibraryEntry(
        source_library_entry_id=entry_id or uuid4(),
        status="saved",
        created_at=now,
        updated_at=now,
        work=work,
        edition=edition,
        assets=assets or [],
        reading_states=readings or [],
        collection_ids=[],
        tag_ids=[],
    )


def _manifest(
    entries: list[PortableLibraryEntry],
    *,
    collections: list[PortableCollection] | None = None,
) -> LibraryPortabilityExportResponse:
    return LibraryPortabilityExportResponse(
        exported_at=datetime.now(UTC),
        entries=entries,
        collections=collections or [],
        tags=[],
        smart_shelves=[],
    )


def _codes(manifest: LibraryPortabilityExportResponse) -> set[str]:
    return {item.code for item in validate_manifest_consistency(manifest)}


def test_repeated_embedded_work_identity_is_valid_when_consistent() -> None:
    work_id = uuid4()
    shared_work = _work(source_id=work_id, lccn="WORK-1")
    first = _entry(
        work=shared_work,
        edition=_edition(title="Volume One", isbn="ISBN-1"),
    )
    second = _entry(
        work=shared_work.model_copy(deep=True),
        edition=_edition(title="Volume Two", isbn="ISBN-2"),
    )

    conflicts = validate_manifest_consistency(_manifest([first, second]))

    assert conflicts == []


def test_manifest_rejects_contradictory_repeated_source_identity() -> None:
    work_id = uuid4()
    first = _entry(work=_work(source_id=work_id, title="First Identity"))
    second = _entry(work=_work(source_id=work_id, title="Contradictory Identity"))

    codes = _codes(_manifest([first, second]))

    assert "manifest_work_source_conflict" in codes
    assert "manifest_library_entry_identity_collision" in codes


def test_manifest_rejects_cross_source_durable_identity_collision() -> None:
    first = _entry(work=_work(lccn="SAME-LCCN"))
    second = _entry(work=_work(lccn="SAME LCCN"))

    codes = _codes(_manifest([first, second]))

    assert "manifest_work_identifier_collision" in codes


def test_manifest_rejects_edition_reused_under_multiple_works() -> None:
    edition = _edition(isbn="SHARED-EDITION")
    first = _entry(
        work=_work(title="First Work", lccn="PARENT-WORK-1"),
        edition=edition,
    )
    second = _entry(
        work=_work(title="Second Work", lccn="PARENT-WORK-2"),
        edition=edition.model_copy(deep=True),
    )

    codes = _codes(_manifest([first, second]))

    assert "manifest_edition_work_conflict" in codes


def test_manifest_rejects_document_reused_under_multiple_assets() -> None:
    edition = _edition(isbn="DOCUMENT-PARENT")
    document = PortableDocumentIdentity(
        source_document_id=uuid4(),
        source_sha256="c" * 64,
        format="EPUB",
        parser_name="epub",
        parser_version="1",
    )
    first_asset = PortableAssetManifest(
        source_asset_id=uuid4(),
        edition=edition.model_copy(deep=True),
        format="EPUB",
        media_type="application/epub+zip",
        byte_size=20,
        content_sha256="c" * 64,
        identifiers=[],
        sources=[],
        document=document,
        rights=None,
        byte_policy=PortableBytePolicy(
            policy_export_allowed=False,
            policy_share_allowed=False,
        ),
    )
    second_asset = first_asset.model_copy(
        update={"source_asset_id": uuid4()},
        deep=True,
    )
    entry = _entry(
        work=_work(lccn="DOCUMENT-WORK"),
        edition=edition,
        assets=[first_asset, second_asset],
    )

    codes = _codes(_manifest([entry]))

    assert "manifest_document_asset_conflict" in codes


def test_manifest_rejects_destination_owned_name_collision() -> None:
    now = datetime.now(UTC)
    collections = [
        PortableCollection(
            source_collection_id=uuid4(),
            name="Primary Sources",
            description=None,
            created_at=now,
            updated_at=now,
        ),
        PortableCollection(
            source_collection_id=uuid4(),
            name="  PRIMARY   SOURCES ",
            description="different portable row",
            created_at=now,
            updated_at=now,
        ),
    ]

    codes = _codes(_manifest([], collections=collections))

    assert "manifest_collection_name_collision" in codes


def test_manifest_rejects_asset_document_and_bookmark_self_contradictions() -> None:
    now = datetime.now(UTC)
    work = _work(lccn="ASSET-WORK")
    edition = _edition(isbn="ASSET-EDITION")
    document_id = uuid4()
    document = PortableDocumentIdentity(
        source_document_id=document_id,
        source_sha256="a" * 64,
        format="EPUB",
        parser_name="epub",
        parser_version="1",
    )
    asset = PortableAssetManifest(
        source_asset_id=uuid4(),
        edition=edition.model_copy(deep=True),
        format="PDF",
        media_type="application/pdf",
        byte_size=10,
        content_sha256="b" * 64,
        identifiers=[],
        sources=[],
        document=document,
        rights=None,
        byte_policy=PortableBytePolicy(
            policy_export_allowed=False,
            policy_share_allowed=False,
        ),
    )
    bookmark_id = uuid4()
    reading = PortableReadingState(
        source_reading_state_id=uuid4(),
        document=document.model_copy(deep=True),
        status="reading",
        progress_fraction=0.2,
        position=None,
        last_read_at=now,
        created_at=now,
        updated_at=now,
        bookmarks=[
            PortableBookmark(
                source_bookmark_id=bookmark_id,
                section_ordinal=0,
                char_offset=5,
                locator={"spine_index": 0},
                label="first",
                created_at=now,
                updated_at=now,
            ),
            PortableBookmark(
                source_bookmark_id=uuid4(),
                section_ordinal=0,
                char_offset=5,
                locator={"spine_index": 0},
                label="second",
                created_at=now,
                updated_at=now,
            ),
        ],
        highlights=[],
    )

    manifest = _manifest(
        [_entry(work=work, edition=edition, assets=[asset], readings=[reading])]
    )
    codes = _codes(manifest)

    assert "manifest_asset_document_sha_conflict" in codes
    assert "manifest_asset_document_format_conflict" in codes
    assert "manifest_duplicate_bookmark_position" in codes


async def test_planner_and_apply_fail_closed_before_mutation(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="manifest-consistency")
    session.add(principal)
    await session.flush()
    manifest = _manifest(
        [
            _entry(work=_work(title="First", lccn="COLLISION-1")),
            _entry(work=_work(title="Second", lccn="COLLISION 1")),
        ]
    )

    planner = LibraryPortabilityImportPlanner(session_scope_factory=_scope(session))
    plan = await planner.plan(principal_id=principal.id, manifest=manifest)

    assert plan.can_apply is False
    assert any(item.code == "manifest_work_identifier_collision" for item in plan.conflicts)

    result = await LibraryPortabilityImportApplier(
        session_scope_factory=_scope(session)
    ).apply(principal_id=principal.id, manifest=manifest)

    assert result.committed is False
    assert result.plan.can_apply is False
    assert result.summary.works_created == 0
    assert await session.scalar(select(func.count()).select_from(Work)) == 0
