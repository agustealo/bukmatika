import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


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
