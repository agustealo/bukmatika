import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import InteractionEvent
from bukmatika.persistence.search import CatalogSearchRepository

DATABASE_URL = os.getenv("BUKMATIKA_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="PostgreSQL integration URL not configured",
)


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


async def test_catalog_search_uses_canonical_title_author_and_subjects(
    session: AsyncSession,
) -> None:
    catalog = CatalogRepository(session)
    work = await catalog.create_work(
        title="Voyages Before Columbus",
        normalized_title="voyages before columbus",
    )
    await catalog.add_author(
        work_id=work.id,
        display_name="Jane Navigator",
        normalized_name="jane navigator",
    )
    await catalog.add_subject(
        work_id=work.id,
        display_name="Maritime trade",
        normalized_name="maritime trade",
    )
    await session.flush()

    search = CatalogSearchRepository(session)
    by_title = await search.search_works(
        query="Voyages Columbus",
        normalized_query="voyages columbus",
        limit=10,
    )
    by_author = await search.search_works(
        query="Jane Navigator",
        normalized_query="jane navigator",
        limit=10,
    )
    by_subject = await search.search_works(
        query="Maritime trade",
        normalized_query="maritime trade",
        limit=10,
    )

    assert [match.work_id for match in by_title] == [work.id]
    assert [match.work_id for match in by_author] == [work.id]
    assert [match.work_id for match in by_subject] == [work.id]
    assert await search.authors_for_work(work.id) == ["Jane Navigator"]


async def test_semantic_interaction_event_is_append_only_evidence(
    session: AsyncSession,
) -> None:
    repository = InteractionEventRepository(session)
    event = await repository.record(
        SemanticEventType.DISCOVERY_SEARCH_COMPLETED,
        context={"session_id": "session-1", "result_count": 4},
    )
    await session.flush()

    stored = await session.scalar(
        select(InteractionEvent).where(InteractionEvent.id == event.id)
    )
    assert stored is not None
    assert stored.event_type == "discovery.search_completed"
    assert stored.context == {"session_id": "session-1", "result_count": 4}
