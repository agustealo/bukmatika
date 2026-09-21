from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import SourceObservation, Work


async def test_source_observation_is_idempotent(session: AsyncSession) -> None:
    repository = CatalogRepository(session)
    source = await repository.upsert_source_record(
        provider="test-provider",
        provider_record_id="record-1",
        canonical_url="https://example.org/books/1",
    )
    payload = {"title": "A Book", "year": 1901}

    first = await repository.record_source_observation(
        source_record_id=source.id,
        payload=payload,
        parser_version="test-v1",
    )
    second = await repository.record_source_observation(
        source_record_id=source.id,
        payload=payload,
        parser_version="test-v1",
    )
    await session.commit()

    assert first.id == second.id
    stored = await session.scalar(select(SourceObservation).where(SourceObservation.id == first.id))
    assert stored is not None
    assert stored.observation_count == 2


async def test_transaction_rollback_does_not_leave_partial_catalog_state(
    session: AsyncSession,
) -> None:
    work = Work(canonical_title="Rollback Book", normalized_title="rollback book")
    session.add(work)
    await session.flush()
    work_id = work.id
    await session.rollback()

    stored = await session.scalar(select(Work).where(Work.id == work_id))
    assert stored is None
