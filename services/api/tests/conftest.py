import os
from collections.abc import AsyncIterator, Generator
from pathlib import Path

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


_INITIAL_READER_ROUTES = _reader_route_snapshot()


@pytest.hookimpl(hookwrapper=True)
def pytest_pycollect_makemodule(
    module_path: Path,
    parent: pytest.Collector,
) -> Generator[None, None, None]:
    del parent
    before = _reader_route_snapshot()
    yield
    after = _reader_route_snapshot()
    assert after == before, (
        f"Collecting {module_path} mutated shared reader routes: "
        f"initial={_INITIAL_READER_ROUTES!r}, before={before!r}, after={after!r}"
    )
    assert after, (
        f"Reader routes absent after collecting {module_path}: "
        f"initial={_INITIAL_READER_ROUTES!r}"
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
