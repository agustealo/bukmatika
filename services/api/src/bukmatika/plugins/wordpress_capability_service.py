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
    WordPressCapabilityObservation,
    probe_wordpress_capabilities,
)
from bukmatika.plugins.wordpress_credentials import load_wordpress_credentials

_EVIDENCE = frozenset(
    {
        "AUTH_CAPABILITIES_200",
        "AUTH_CAPABILITY_INPUT_INVALID",
        "AUTH_CAPABILITY_TRANSPORT_UNAVAILABLE",
        "AUTH_CAPABILITY_REJECTED",
        "AUTH_CAPABILITY_HTTP_UNEXPECTED",
        "AUTH_CAPABILITY_RESPONSE_INVALID",
        "AUTH_CAPABILITY_SCHEMA_INVALID",
    }
)


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
    if (
        observation.evidence_code not in _EVIDENCE
        or observation.state not in {ConnectionHealth.VERIFIED, ConnectionHealth.UNAVAILABLE}
        or observation.capabilities_observed != (
            observation.evidence_code == "AUTH_CAPABILITIES_200"
        )
    ):
        raise ValueError("Unrecognized WordPress capability observation")
    if observation.state is ConnectionHealth.UNAVAILABLE and any(
        observation.capabilities.values()
    ):
        raise ValueError("Unavailable capability proof cannot grant permissions")
    await PluginHealthReceiptRepository(session).record(
        principal_id=principal_id,
        connection_id=connection_id,
        state=observation.state,
        evidence_code=observation.evidence_code,
        checked_at=datetime.now(UTC),
    )
    return observation
