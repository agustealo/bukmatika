from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_library_portability_apply import _manifest as _apply_manifest
from test_library_portability_apply import _scope as _apply_scope
from test_library_portability_apply import (
    _seed_destination as _seed_apply_destination,
)
from test_library_portability_import import _manifest as _plan_manifest
from test_library_portability_import import _scope as _plan_scope
from test_library_portability_import import (
    _seed_destination as _seed_plan_destination,
)

from bukmatika.library import LibraryPortabilityImportPlanner
from bukmatika.library.portability_apply import LibraryPortabilityImportApplier
from bukmatika.library.portability_domain import LibraryPortabilityExportResponse
from bukmatika.persistence.library_organization_models import (
    LibraryCollection,
    LibrarySmartShelf,
    LibraryTag,
)
from bukmatika.persistence.reader_models import ReadingState


def _forge_unread_recency(
    manifest: LibraryPortabilityExportResponse,
) -> None:
    reading = manifest.entries[0].reading_states[0]
    reading.status = "unread"
    reading.progress_fraction = 0.0
    reading.last_read_at = datetime.now(UTC)


async def test_import_plan_rejects_unread_reader_state_with_last_read_recency(
    session: AsyncSession,
) -> None:
    principal, _, _, _, document, _ = await _seed_plan_destination(
        session,
        suffix="recency-plan",
    )
    assert document is not None
    manifest = _plan_manifest(
        suffix="recency-plan",
        document_sha=document.source_sha256,
    )
    _forge_unread_recency(manifest)

    result = await LibraryPortabilityImportPlanner(
        session_scope_factory=_plan_scope(session)
    ).plan(principal_id=principal.id, manifest=manifest)

    assert result.can_apply is False
    assert any(
        conflict.code == "manifest_unread_reader_recency_conflict"
        for conflict in result.conflicts
    )


async def test_import_apply_rejects_unread_recency_before_any_mutation(
    session: AsyncSession,
) -> None:
    principal, _, document, _ = await _seed_apply_destination(
        session,
        suffix="recency-apply",
    )
    manifest = _apply_manifest(
        suffix="recency-apply",
        document_sha=document.source_sha256,
        reading_updated_at=datetime.now(UTC),
    )
    _forge_unread_recency(manifest)

    result = await LibraryPortabilityImportApplier(
        session_scope_factory=_apply_scope(session)
    ).apply(principal_id=principal.id, manifest=manifest)

    assert result.committed is False
    assert result.plan.can_apply is False
    assert any(
        conflict.code == "manifest_unread_reader_recency_conflict"
        for conflict in result.plan.conflicts
    )
    assert await session.scalar(select(func.count()).select_from(ReadingState)) == 0
    assert await session.scalar(select(func.count()).select_from(LibraryCollection)) == 0
    assert await session.scalar(select(func.count()).select_from(LibraryTag)) == 0
    assert await session.scalar(select(func.count()).select_from(LibrarySmartShelf)) == 0
