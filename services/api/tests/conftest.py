import os
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

_READER_PREFIX = "/v1/library/{library_entry_id}/documents/{document_id}"


def _reader_route_snapshot() -> tuple[tuple[str | None, str | None], ...]:
    from bukmatika.main import app

    return tuple(
        (getattr(route, "name", None), getattr(route, "path", None))
        for route in app.routes
        if str(getattr(route, "path", "")).startswith(_READER_PREFIX)
    )


@pytest.fixture(autouse=True)
def reader_routes_are_not_polluted(request: pytest.FixtureRequest) -> Iterator[None]:
    before = _reader_route_snapshot()
    assert before, f"Reader routes missing before {request.node.nodeid}"
    yield
    after = _reader_route_snapshot()
    assert after == before, (
        f"{request.node.nodeid} mutated shared reader routes: before={before!r}, after={after!r}"
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    database_url = os.getenv("BUKMATIKA_DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL integration URL not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    async with engine.connect() as connection:
        outer_transaction = await connection.begin()
        value = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield value
        finally:
            await value.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
    await engine.dispose()
