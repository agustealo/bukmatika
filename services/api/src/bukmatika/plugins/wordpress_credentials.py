"""Reusable principal-scoped WordPress REST credential access.

Call only within the caller's transaction. A row lock fences configuration
rotation while a bounded authenticated read is taking place.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import (
    CredentialReference,
    CredentialUnavailable,
    DatabaseSecretStore,
    InstallationCredentialKey,
    SecretOwner,
)
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.persistence.secret_models import EncryptedSecret


@dataclass(frozen=True, slots=True, repr=False)
class WordPressCredentials:
    site_url: str
    username: str
    application_password: str


async def load_wordpress_credentials(
    session: AsyncSession,
    *,
    principal_id: UUID,
    connection_id: UUID,
    installation_key: InstallationCredentialKey,
) -> WordPressCredentials:
    """Read one owner's secret only after taking the connection's row lock."""
    connection = await session.scalar(
        select(PluginConnection)
        .where(
            PluginConnection.id == connection_id,
            PluginConnection.principal_id == principal_id,
            PluginConnection.plugin_id == "wordpress",
            PluginConnection.mode_id == "rest",
        )
        .with_for_update()
    )
    if connection is None:
        raise LookupError("WordPress REST connection is unavailable")
    site_url = connection.settings.get("site_url")
    username = connection.settings.get("username")
    if not isinstance(site_url, str) or not isinstance(username, str):
        raise ValueError("WordPress connection configuration is invalid")
    owner = SecretOwner(
        principal_id=principal_id,
        owner_kind="plugin_connection",
        owner_id=connection.id,
        slot="application_password",
    )
    secret_id = await session.scalar(
        select(EncryptedSecret.id).where(
            EncryptedSecret.principal_id == principal_id,
            EncryptedSecret.owner_kind == owner.owner_kind,
            EncryptedSecret.owner_id == connection.id,
            EncryptedSecret.slot == owner.slot,
        )
    )
    if secret_id is None:
        raise CredentialUnavailable("WordPress Application Password is unavailable")
    secret = await DatabaseSecretStore(session, installation_key=installation_key).reveal(
        owner=owner, reference=CredentialReference(f"credential:{secret_id}")
    )
    return WordPressCredentials(
        site_url=site_url, username=username, application_password=secret
    )
