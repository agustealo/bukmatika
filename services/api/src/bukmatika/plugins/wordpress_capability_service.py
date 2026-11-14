"""Principal-scoped, live WordPress capability inspection.

Never turn observed capability metadata into write authorization. Callers must
enforce policy and validate each actual WordPress mutation separately.
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.wordpress_capability_probe import (
    WORDPRESS_CAPABILITY_EVIDENCE_CODES,
    WORDPRESS_CAPABILITY_KEYS,
    WordPressCapabilityObservation,
    probe_wordpress_capabilities,
)
from bukmatika.plugins.wordpress_capability_snapshots import (
    WordPressCapabilitySnapshotRepository,
)
from bukmatika.plugins.wordpress_credentials import load_wordpress_credentials


async def verify_wordpress_capabilities(
    session: AsyncSession,
    *,
    principal_id: UUID,
    connection_id: UUID,
    installation_key: InstallationCredentialKey,
) -> WordPressCapabilityObservation:
    """Perform a bounded authenticated read while holding connection ownership."""
    credentials = await load_wordpress_credentials(
        session,
        principal_id=principal_id,
        connection_id=connection_id,
        installation_key=installation_key,
    )
    observation = await asyncio.to_thread(
        probe_wordpress_capabilities,
        credentials.site_url,
        credentials.username,
        credentials.application_password,
    )
    expected_state = (
        ConnectionHealth.VERIFIED
        if observation.evidence_code == "AUTH_CAPABILITIES_200"
        else ConnectionHealth.UNAVAILABLE
    )
    if (
        observation.evidence_code not in WORDPRESS_CAPABILITY_EVIDENCE_CODES
        or observation.state is not expected_state
    ):
        raise ValueError("Unrecognized WordPress capability observation")
    if set(observation.capabilities) != set(WORDPRESS_CAPABILITY_KEYS) or any(
        type(value) is not bool for value in observation.capabilities.values()
    ):
        raise ValueError("WordPress capability observation has invalid fields")
    if observation.state is ConnectionHealth.UNAVAILABLE and any(
        observation.capabilities.values()
    ):
        raise ValueError("Unavailable capability proof cannot grant permissions")
    checked_at = datetime.now(UTC)
    await PluginHealthReceiptRepository(session).record(
        principal_id=principal_id,
        connection_id=connection_id,
        state=observation.state,
        evidence_code=observation.evidence_code,
        checked_at=checked_at,
    )
    await WordPressCapabilitySnapshotRepository(session).record(
        principal_id=principal_id,
        connection_id=connection_id,
        observation=observation,
        checked_at=checked_at,
    )
    return observation
