from sqlalchemy.ext.asyncio import AsyncSession
from test_library import _scope, _seed_dossier

from bukmatika.library import LibraryService
from bukmatika.library.readiness import LibraryReadinessService
from bukmatika.persistence.models import Edition, LibraryEntry, Principal, Work


async def test_library_readiness_counts_owned_and_readable_entries(
    session: AsyncSession,
) -> None:
    principal, _, edition, _, _, _ = await _seed_dossier(session, suffix="readiness")
    readiness = LibraryReadinessService(session_scope_factory=_scope(session))
    library = LibraryService(session_scope_factory=_scope(session))

    empty = await readiness.readiness(principal_id=principal.id)
    assert empty.entry_count == 0
    assert empty.readable_entry_count == 0

    await library.save_edition(principal_id=principal.id, edition_id=edition.id)

    ready = await readiness.readiness(principal_id=principal.id)
    assert ready.entry_count == 1
    assert ready.readable_entry_count == 1


async def test_library_readiness_distinguishes_saved_unprocessed_entries(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="readiness-unprocessed")
    work = Work(canonical_title="Pending readiness", normalized_title="pending readiness")
    session.add_all((principal, work))
    await session.flush()

    edition = Edition(work_id=work.id, title="Pending readiness edition")
    session.add(edition)
    await session.flush()

    session.add(
        LibraryEntry(
            principal_id=principal.id,
            work_id=work.id,
            edition_id=edition.id,
            status="saved",
        )
    )
    await session.flush()

    response = await LibraryReadinessService(session_scope_factory=_scope(session)).readiness(
        principal_id=principal.id
    )

    assert response.entry_count == 1
    assert response.readable_entry_count == 0
