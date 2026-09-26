from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.database import session_scope

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def content_sha256_lock_id(sha256: str) -> int:
    digest_bytes = bytes.fromhex(sha256)
    if len(digest_bytes) != 32:
        raise ValueError("Content SHA-256 must contain exactly 32 bytes")
    return int.from_bytes(digest_bytes[:8], byteorder="big", signed=True)


@asynccontextmanager
async def content_publication_lock(
    sha256: str,
    *,
    session_scope_factory: SessionScopeFactory = session_scope,
) -> AsyncIterator[None]:
    """Serialize canonical byte publication and erasure for one content identity.

    This is deliberately a PostgreSQL session-level advisory lock. It can span the producer's
    existing application transactions, so the lock starts before filesystem publication and is
    released only after the caller has finished its canonical database association.
    """

    lock_id = content_sha256_lock_id(sha256)
    async with session_scope_factory() as lock_session:
        await lock_session.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": lock_id},
        )
        try:
            yield
        finally:
            await lock_session.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": lock_id},
            )


__all__ = ["content_publication_lock", "content_sha256_lock_id"]
