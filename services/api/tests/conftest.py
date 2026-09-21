import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from bukmatika.persistence.models import Base


async def _clear_application_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(table.delete())


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    database_url = os.getenv("BUKMATIKA_DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL integration URL not configured")

    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _clear_application_tables(engine)
    try:
        async with factory() as value:
            yield value
    finally:
        await _clear_application_tables(engine)
        await engine.dispose()
