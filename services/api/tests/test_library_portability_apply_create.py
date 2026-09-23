from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.portability_apply import LibraryPortabilityImportApplier
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableEditionIdentity,
    PortableIdentifier,
    PortableLibraryEntry,
    PortableSourceReference,
    PortableWorkIdentity,
)
from bukmatika.persistence.models import (
    Edition,
    Identifier,
    LibraryEntry,
    Principal,
    SourceRecord,
    SourceRecordLink,
    Work,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


def _metadata_only_manifest() -> LibraryPortabilityExportResponse:
    now = datetime.now(UTC)
    work = PortableWorkIdentity(
        source_work_id="11111111-1111-4111-8111-111111111111",
        canonical_title="A Portable History",
        authors=["Ada Researcher"],
        subjects=["History"],
        identifiers=[PortableIdentifier(scheme="lccn", value="PORTABLE-001")],
        sources=[
            PortableSourceReference(
                provider="fixture-provider",
                provider_record_id="portable-work-001",
                canonical_url="https://catalog.example/portable-work-001",
                relationship="describes",
            )
        ],
    )
    edition = PortableEditionIdentity(
        source_edition_id="22222222-2222-4222-8222-222222222222",
        title="A Portable History, First Edition",
        language="en",
        publication_year=1901,
        publisher="Open Press",
        edition_statement="First edition",
        identifiers=[PortableIdentifier(scheme="isbn", value="978-0-00-000001-1")],
        sources=[
            PortableSourceReference(
                provider="fixture-provider",
                provider_record_id="portable-edition-001",
                canonical_url="https://catalog.example/portable-edition-001",
                relationship="describes",
            )
        ],
    )
    return LibraryPortabilityExportResponse(
        exported_at=now,
        entries=[
            PortableLibraryEntry(
                source_library_entry_id="33333333-3333-4333-8333-333333333333",
                status="saved",
                created_at=now,
                updated_at=now,
                work=work,
                edition=edition,
                assets=[],
                reading_states=[],
                collection_ids=[],
                tag_ids=[],
            )
        ],
        collections=[],
        tags=[],
        smart_shelves=[],
    )


async def test_import_apply_creates_metadata_once_then_matches_it(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="apply-create")
    session.add(principal)
    await session.flush()
    manifest = _metadata_only_manifest()
    applier = LibraryPortabilityImportApplier(session_scope_factory=_scope(session))

    first = await applier.apply(principal_id=principal.id, manifest=manifest)

    assert first.committed is True
    assert first.plan.can_apply is True
    assert first.summary.works_created == 1
    assert first.summary.editions_created == 1
    assert first.summary.library_entries_created == 1

    work = await session.scalar(
        select(Work).where(Work.normalized_title == "a portable history")
    )
    assert work is not None
    edition = await session.scalar(
        select(Edition).where(
            Edition.work_id == work.id,
            Edition.title == "A Portable History, First Edition",
        )
    )
    assert edition is not None
    assert edition.edition_statement == "First edition"
    entry = await session.scalar(
        select(LibraryEntry).where(
            LibraryEntry.principal_id == principal.id,
            LibraryEntry.edition_id == edition.id,
        )
    )
    assert entry is not None

    second = await applier.apply(principal_id=principal.id, manifest=manifest)

    assert second.committed is True
    assert second.plan.can_apply is True
    assert second.summary.works_created == 0
    assert second.summary.editions_created == 0
    assert second.summary.library_entries_created == 0
    assert second.plan.entries[0].work.destination_id == work.id
    assert second.plan.entries[0].edition is not None
    assert second.plan.entries[0].edition.destination_id == edition.id
    assert second.plan.entries[0].library_entry.destination_id == entry.id

    assert await session.scalar(
        select(func.count()).select_from(Work).where(Work.normalized_title == "a portable history")
    ) == 1
    assert await session.scalar(
        select(func.count()).select_from(Edition).where(Edition.work_id == work.id)
    ) == 1
    assert await session.scalar(
        select(func.count()).select_from(LibraryEntry).where(
            LibraryEntry.principal_id == principal.id,
            LibraryEntry.edition_id == edition.id,
        )
    ) == 1

    identifiers = list(
        (
            await session.scalars(
                select(Identifier).where(
                    Identifier.entity_id.in_([work.id, edition.id])
                )
            )
        ).all()
    )
    assert {(item.entity_type, item.scheme) for item in identifiers} == {
        ("work", "lccn"),
        ("edition", "isbn"),
    }
    source_links = (
        await session.execute(
            select(SourceRecord.provider_record_id, SourceRecordLink.entity_type)
            .join(SourceRecordLink, SourceRecordLink.source_record_id == SourceRecord.id)
            .where(SourceRecordLink.entity_id.in_([work.id, edition.id]))
        )
    ).all()
    assert set(source_links) == {
        ("portable-work-001", "work"),
        ("portable-edition-001", "edition"),
    }
