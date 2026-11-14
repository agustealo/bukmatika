"""Principal-scoped identity verification using the canonical encrypted secret vault.

No account details or decrypted credentials are returned or persisted.
Public route activation remains outside this module's responsibility.
"""

import asyncio
from datetime import UTC, datetime
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
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.wordpress_identity_probe import (
    WordPressIdentityObservation,
    probe_wordpress_identity,
)


async def verify_wordpress_identity(
    session: AsyncSession,
    *,
    principal_id: UUID,
    connection_id: UUID,
    installation_key: InstallationCredentialKey,
) -> WordPressIdentityObservation:
    """Check saved Application Password identity, with connection-scoped locking.

    The caller's transaction owns the lock until the observation is recorded,
    serializing concurrent credential rotation and identity verification.
    """
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
    observation = await asyncio.to_thread(
        probe_wordpress_identity, site_url, username, secret
    )
    if observation.evidence_code not in {
        "AUTH_IDENTITY_200",
        "AUTH_INPUT_INVALID",
        "AUTH_TRANSPORT_UNAVAILABLE",
        "AUTH_REJECTED",
        "AUTH_HTTP_UNEXPECTED",
        "AUTH_RESPONSE_INVALID",
        "AUTH_IDENTITY_INVALID",
    } or observation.state not in {
        ConnectionHealth.VERIFIED,
        ConnectionHealth.UNAVAILABLE,
    }:
        raise ValueError("Unsupported WordPress identity observation")

    await PluginHealthReceiptRepository(session).record(
        principal_id=principal_id,
        connection_id=connection.id,
        state=observation.state,
        evidence_code=observation.evidence_code,
        checked_at=datetime.now(UTC),
    )
    return observation
