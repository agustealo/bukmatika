from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import InteractionEvent
from bukmatika.persistence.search import CatalogSearchRepository


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
