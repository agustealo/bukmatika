from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status

from bukmatika.config import get_settings
from bukmatika.identity.domain import SessionResponse
from bukmatika.persistence import session_scope
from bukmatika.persistence.identity import AuthenticatedPrincipal, PrincipalSessionRepository

settings = get_settings()
router = APIRouter(prefix="/v1/session", tags=["session"])


def _session_response(identity: AuthenticatedPrincipal) -> SessionResponse:
    return SessionResponse(
        principal_id=identity.principal_id,
        session_id=identity.session_id,
        expires_at=identity.expires_at,
    )


async def optional_principal(
    raw_token: Annotated[str | None, Cookie(alias=settings.local_session_cookie_name)] = None,
) -> AuthenticatedPrincipal | None:
    if raw_token is None:
        return None
    async with session_scope() as database_session:
        return await PrincipalSessionRepository(database_session).authenticate(raw_token)


async def require_principal(
    identity: Annotated[AuthenticatedPrincipal | None, Depends(optional_principal)],
) -> AuthenticatedPrincipal:
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "SESSION_REQUIRED"},
        )
    return identity


@router.post("/local", response_model=SessionResponse)
async def bootstrap_local_session(
    response: Response,
    raw_token: Annotated[str | None, Cookie(alias=settings.local_session_cookie_name)] = None,
) -> SessionResponse:
    async with session_scope() as database_session:
        repository = PrincipalSessionRepository(database_session)
        identity = await repository.authenticate(raw_token) if raw_token is not None else None
        if identity is None:
            created = await repository.create_local(ttl_days=settings.local_session_ttl_days)
            identity = created.identity
            _set_session_cookie(response, created.raw_token, identity.expires_at)
        return _session_response(identity)


@router.get("", response_model=SessionResponse)
async def current_session(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> SessionResponse:
    return _session_response(identity)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_local_session(
    response: Response,
    raw_token: Annotated[str | None, Cookie(alias=settings.local_session_cookie_name)] = None,
) -> Response:
    if raw_token is not None:
        async with session_scope() as database_session:
            await PrincipalSessionRepository(database_session).revoke(raw_token)
    response.delete_cookie(
        key=settings.local_session_cookie_name,
        path="/",
        secure=settings.local_session_secure_cookie,
        httponly=True,
        samesite="lax",
    )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


def _set_session_cookie(response: Response, raw_token: str, expires_at: datetime) -> None:
    remaining = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=settings.local_session_cookie_name,
        value=raw_token,
        max_age=remaining,
        expires=expires_at,
        path="/",
        secure=settings.local_session_secure_cookie,
        httponly=True,
        samesite="lax",
    )


__all__ = ["optional_principal", "require_principal", "router"]
