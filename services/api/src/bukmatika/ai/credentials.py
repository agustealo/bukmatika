from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import (
    CredentialIntegrityError,
    CredentialKeyUnavailable,
    CredentialReference,
    CredentialUnavailable,
    DatabaseSecretStore,
    InstallationCredentialKey,
    SecretOwner,
)
from bukmatika.persistence.providers import ProviderConnectionRepository

_AI_OWNER_KIND = "ai_provider_connection"
_AI_SECRET_SLOT = "api_key"


class CredentialStore(Protocol):
    async def replace(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        secret: str,
    ) -> CredentialReference: ...

    async def reveal(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        reference: CredentialReference,
    ) -> str: ...

    async def delete(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> None: ...


class DatabaseCredentialStore:
    """AI-provider compatibility adapter over Bukmatika's canonical secret store."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        installation_key: InstallationCredentialKey,
    ) -> None:
        self._session = session
        self._secrets = DatabaseSecretStore(
            session,
            installation_key=installation_key,
        )

    async def replace(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        secret: str,
    ) -> CredentialReference:
        connection = await ProviderConnectionRepository(self._session).get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
        )
        reference = await self._secrets.replace(
            owner=_owner(principal_id=principal_id, connection_id=connection_id),
            secret=secret,
        )
        connection.credential_reference = reference.value
        await self._session.flush()
        return reference

    async def reveal(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        reference: CredentialReference,
    ) -> str:
        connection = await ProviderConnectionRepository(self._session).get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
            enabled_only=True,
        )
        if connection.credential_reference != reference.value:
            raise CredentialUnavailable("Credential reference is no longer active")
        return await self._secrets.reveal(
            owner=_owner(principal_id=principal_id, connection_id=connection_id),
            reference=reference,
        )

    async def delete(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> None:
        connection = await ProviderConnectionRepository(self._session).get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
        )
        await self._secrets.delete(
            owner=_owner(principal_id=principal_id, connection_id=connection_id)
        )
        connection.credential_reference = None
        await self._session.flush()


def _owner(*, principal_id: UUID, connection_id: UUID) -> SecretOwner:
    return SecretOwner(
        principal_id=principal_id,
        owner_kind=_AI_OWNER_KIND,
        owner_id=connection_id,
        slot=_AI_SECRET_SLOT,
    )


__all__ = [
    "CredentialIntegrityError",
    "CredentialKeyUnavailable",
    "CredentialReference",
    "CredentialStore",
    "CredentialUnavailable",
    "DatabaseCredentialStore",
    "InstallationCredentialKey",
]
