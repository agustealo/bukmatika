from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def content_sha256_lock_id(sha256: str) -> int:
    digest_bytes = bytes.fromhex(sha256)
    if len(digest_bytes) != 32:
        raise ValueError("Content SHA-256 must contain exactly 32 bytes")
    return int.from_bytes(digest_bytes[:8], byteorder="big", signed=True)


async def lock_content_sha256(session: AsyncSession, sha256: str) -> None:
    """Serialize canonical file publication and erasure for one content identity.

    The PostgreSQL transaction-level advisory lock is held until the caller's transaction ends.
    Every path that makes a content SHA live must participate in this lock before publishing bytes.
    """

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": content_sha256_lock_id(sha256)},
    )


__all__ = ["content_sha256_lock_id", "lock_content_sha256"]
