import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import SourceObservation, Work

DATABASE_URL = os.getenv("BUKMATIKA_DATABASE_URL")
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="PostgreSQL integration URL not configured")


@pytest.fixture
async def session() -> AsyncSession:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        try:
            yield value
        finally:
            await value.rollback()
            await value.close()
    await engine.dispose()


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
