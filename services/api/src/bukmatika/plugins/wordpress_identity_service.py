"""Principal-scoped identity verification using the canonical encrypted secret vault.

No account details or decrypted credentials are returned or persisted.
Public route activation remains outside this module's responsibility.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import (
    InstallationCredentialKey,
)
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.wordpress_credentials import load_wordpress_credentials
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
    credentials = await load_wordpress_credentials(
        session,
        principal_id=principal_id,
        connection_id=connection_id,
        installation_key=installation_key,
    )
    observation = await asyncio.to_thread(
        probe_wordpress_identity,
        credentials.site_url,
        credentials.username,
        credentials.application_password,
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
