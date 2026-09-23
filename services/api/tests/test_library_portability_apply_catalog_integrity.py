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
    Contributor,
    Edition,
    Identifier,
    LibraryEntry,
    Principal,
    SourceRecord,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


async def test_import_match_does_not_poison_shared_catalog_metadata(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="apply-catalog-integrity")
    work = Work(canonical_title="Canonical Work", normalized_title="canonical work")
    session.add_all([principal, work])
    await session.flush()
    author = Contributor(display_name="Canonical Author", normalized_name="canonical author")
    session.add(author)
    await session.flush()
    session.add(WorkContributor(work_id=work.id, contributor_id=author.id, role="author"))
    edition = Edition(
        work_id=work.id,
        title="Canonical Edition",
        language="en",
        publication_year=1900,
        publisher="Canonical Press",
        edition_statement="Canonical statement",
    )
    session.add(edition)
    await session.flush()
    session.add_all(
        [
            Identifier(
                entity_type="work",
                entity_id=work.id,
                scheme="lccn",
                value="CANONICAL-WORK",
                normalized_value="canonicalwork",
            ),
            Identifier(
                entity_type="edition",
                entity_id=edition.id,
                scheme="isbn",
                value="978-CANONICAL",
                normalized_value="978canonical",
            ),
        ]
    )
    await session.flush()

    now = datetime.now(UTC)
    manifest = LibraryPortabilityExportResponse(
        exported_at=now,
        entries=[
            PortableLibraryEntry(
                source_library_entry_id="33333333-3333-4333-8333-333333333333",
                status="saved",
                created_at=now,
                updated_at=now,
                work=PortableWorkIdentity(
                    source_work_id="11111111-1111-4111-8111-111111111111",
                    canonical_title="Portable Rename Attempt",
                    authors=["Portable Author"],
                    subjects=["Portable Subject"],
                    identifiers=[
                        PortableIdentifier(scheme="lccn", value="CANONICAL-WORK"),
                        PortableIdentifier(scheme="oclc", value="PORTABLE-EXTRA-WORK-ID"),
                    ],
                    sources=[
                        PortableSourceReference(
                            provider="portable-provider",
                            provider_record_id="portable-work-record",
                            canonical_url="https://portable.example/work",
                            relationship="describes",
                        )
                    ],
                ),
                edition=PortableEditionIdentity(
                    source_edition_id="22222222-2222-4222-8222-222222222222",
                    title="Portable Edition Rename Attempt",
                    language="en",
                    publication_year=1900,
                    publisher="Portable Press",
                    edition_statement="Portable statement",
                    identifiers=[
                        PortableIdentifier(scheme="isbn", value="978-CANONICAL"),
                        PortableIdentifier(scheme="oclc", value="PORTABLE-EXTRA-EDITION-ID"),
                    ],
                    sources=[
                        PortableSourceReference(
                            provider="portable-provider",
                            provider_record_id="portable-edition-record",
                            canonical_url="https://portable.example/edition",
                            relationship="describes",
                        )
                    ],
                ),
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

    result = await LibraryPortabilityImportApplier(
        session_scope_factory=_scope(session)
    ).apply(principal_id=principal.id, manifest=manifest)

    assert result.committed is True
    assert result.plan.entries[0].work.action == "match"
    assert result.plan.entries[0].work.destination_id == work.id
    assert result.plan.entries[0].edition is not None
    assert result.plan.entries[0].edition.action == "match"
    assert result.plan.entries[0].edition.destination_id == edition.id
    assert result.summary.works_created == 0
    assert result.summary.editions_created == 0
    assert result.summary.library_entries_created == 1

    await session.refresh(work)
    await session.refresh(edition)
    assert work.canonical_title == "Canonical Work"
    assert edition.title == "Canonical Edition"
    assert edition.publisher == "Canonical Press"
    assert edition.edition_statement == "Canonical statement"

    author_names = set(
        (
            await session.scalars(
                select(Contributor.display_name)
                .join(WorkContributor, WorkContributor.contributor_id == Contributor.id)
                .where(WorkContributor.work_id == work.id)
            )
        ).all()
    )
    assert author_names == {"Canonical Author"}
    assert await session.scalar(
        select(func.count())
        .select_from(Subject)
        .join(WorkSubject, WorkSubject.subject_id == Subject.id)
        .where(WorkSubject.work_id == work.id)
    ) == 0
    assert await session.scalar(
        select(func.count()).select_from(Identifier).where(
            Identifier.entity_id.in_([work.id, edition.id])
        )
    ) == 2
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 0
    assert await session.scalar(
        select(func.count()).select_from(LibraryEntry).where(
            LibraryEntry.principal_id == principal.id,
            LibraryEntry.edition_id == edition.id,
        )
    ) == 1
