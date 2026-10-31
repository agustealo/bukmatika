"""Persist public REST transport observations without asserting credential validity."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.wordpress_probe import (
    WordPressProbeObservation,
    probe_public_wordpress_rest,
)


async def record_public_wordpress_probe(
    session: AsyncSession,
    *,
    principal_id: UUID,
    connection_id: UUID,
) -> WordPressProbeObservation:
    """Probe a principal-owned REST connection with no password transmission.

    This deliberately does not grant plugin activation or verify saved credentials.
    The probe executes in a worker thread to avoid blocking the async API loop.
    """
    connection = await session.scalar(
        select(PluginConnection).where(
            PluginConnection.id == connection_id,
            PluginConnection.principal_id == principal_id,
            PluginConnection.plugin_id == "wordpress",
            PluginConnection.mode_id == "rest",
        )
    )
    if connection is None:
        raise LookupError("WordPress REST connection is unavailable")
    site_url = connection.settings.get("site_url")
    if not isinstance(site_url, str):
        raise ValueError("WordPress REST connection has no valid site URL")
    observation = await asyncio.to_thread(probe_public_wordpress_rest, site_url)
    if observation.state not in {ConnectionHealth.VERIFIED, ConnectionHealth.UNAVAILABLE}:
        raise ValueError("Probe did not return an observed state")
    if not observation.evidence_code.startswith(
        ("PUBLIC_REST_", "TRANSPORT_", "HTTP_", "INVALID_")
    ):
        raise ValueError("Unexpected public probe evidence")
    await PluginHealthReceiptRepository(session).record(
        principal_id=principal_id,
        connection_id=connection.id,
        state=observation.state,
        evidence_code=observation.evidence_code,
        checked_at=datetime.now(UTC),
    )
    return observation
