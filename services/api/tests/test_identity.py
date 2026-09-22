from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.identity import PrincipalSessionRepository
from bukmatika.persistence.identity_models import PrincipalSession
from bukmatika.persistence.models import Principal


async def test_local_session_stores_only_token_digest_and_authenticates(
    session: AsyncSession,
) -> None:
    repository = PrincipalSessionRepository(session)
    created = await repository.create_local(ttl_days=30)

    stored = await session.scalar(
        select(PrincipalSession).where(PrincipalSession.id == created.identity.session_id)
    )
    assert stored is not None
    assert stored.token_sha256 != created.raw_token
    assert len(stored.token_sha256) == 64

    principal = await session.get(Principal, created.identity.principal_id)
    assert principal is not None
    assert principal.kind == "local"

    authenticated = await repository.authenticate(created.raw_token)
    assert authenticated is not None
    assert authenticated.principal_id == created.identity.principal_id
    assert authenticated.session_id == created.identity.session_id


async def test_revoked_and_expired_sessions_fail_closed(session: AsyncSession) -> None:
    repository = PrincipalSessionRepository(session)
    revoked = await repository.create_local(ttl_days=30)
    assert await repository.revoke(revoked.raw_token) is True
    assert await repository.authenticate(revoked.raw_token) is None

    expired = await repository.create_local(ttl_days=30)
    stored = await session.scalar(
        select(PrincipalSession).where(PrincipalSession.id == expired.identity.session_id)
    )
    assert stored is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    assert await repository.authenticate(expired.raw_token) is None


async def test_invalid_session_tokens_do_not_match(session: AsyncSession) -> None:
    repository = PrincipalSessionRepository(session)
    created = await repository.create_local(ttl_days=30)

    assert await repository.authenticate("not-the-token") is None
    assert await repository.authenticate("") is None
    assert await repository.authenticate("x" * 513) is None
    assert await repository.authenticate(created.raw_token) is not None
