import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.identity_models import PrincipalSession
from bukmatika.persistence.models import Principal


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    principal_id: UUID
    session_id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NewPrincipalSession:
    identity: AuthenticatedPrincipal
    raw_token: str


class PrincipalSessionRepository:
    """Canonical local-first principal/session authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_local(self, *, ttl_days: int) -> NewPrincipalSession:
        now = datetime.now(UTC)
        principal = Principal(
            kind="local",
            external_subject=f"local:{uuid4()}",
        )
        self._session.add(principal)
        await self._session.flush()

        raw_token = secrets.token_urlsafe(32)
        session = PrincipalSession(
            principal_id=principal.id,
            token_sha256=_token_digest(raw_token),
            expires_at=now + timedelta(days=ttl_days),
            last_seen_at=now,
        )
        self._session.add(session)
        await self._session.flush()
        return NewPrincipalSession(
            identity=AuthenticatedPrincipal(
                principal_id=principal.id,
                session_id=session.id,
                expires_at=session.expires_at,
            ),
            raw_token=raw_token,
        )

    async def authenticate(self, raw_token: str) -> AuthenticatedPrincipal | None:
        if not raw_token or len(raw_token) > 512:
            return None
        now = datetime.now(UTC)
        session = await self._session.scalar(
            select(PrincipalSession)
            .where(
                PrincipalSession.token_sha256 == _token_digest(raw_token),
                PrincipalSession.revoked_at.is_(None),
                PrincipalSession.expires_at > now,
            )
            .with_for_update()
        )
        if session is None:
            return None
        session.last_seen_at = now
        await self._session.flush()
        return AuthenticatedPrincipal(
            principal_id=session.principal_id,
            session_id=session.id,
            expires_at=session.expires_at,
        )

    async def revoke(self, raw_token: str) -> bool:
        if not raw_token or len(raw_token) > 512:
            return False
        session = await self._session.scalar(
            select(PrincipalSession)
            .where(
                PrincipalSession.token_sha256 == _token_digest(raw_token),
                PrincipalSession.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if session is None:
            return False
        session.revoked_at = datetime.now(UTC)
        await self._session.flush()
        return True


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
