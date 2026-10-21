from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.installations import PluginInstallationRepository


async def test_receipts_are_principal_scoped_and_observation_only(
    session: AsyncSession,
) -> None:
    first = Principal(kind="local", external_subject=f"receipt-{uuid4()}")
    other = Principal(kind="local", external_subject=f"receipt-{uuid4()}")
    session.add_all([first, other])
    await session.flush()
    installation = await PluginInstallationRepository(
        session, build_builtin_plugin_registry()
    ).install(principal_id=first.id, plugin_id="wordpress")
    connection = PluginConnection(
        principal_id=first.id,
        plugin_id="wordpress",
        installation_id=installation.id,
        mode_id="rest",
        schema_version=1,
        settings={"site_url": "https://example.com"},
    )
    session.add(connection)
    await session.flush()
    repo = PluginHealthReceiptRepository(session)
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        await repo.record(
            principal_id=first.id,
            connection_id=connection.id,
            state=ConnectionHealth.NOT_TESTED,
            evidence_code="CONFIGURED",
            checked_at=now,
        )
    with pytest.raises(LookupError):
        await repo.record(
            principal_id=other.id,
            connection_id=connection.id,
            state=ConnectionHealth.VERIFIED,
            evidence_code="HTTP_200",
            checked_at=now,
        )
    record = await repo.record(
        principal_id=first.id,
        connection_id=connection.id,
        state=ConnectionHealth.UNAVAILABLE,
        evidence_code="CONNECT_TIMEOUT",
        checked_at=now,
    )
    assert (await repo.latest(principal_id=first.id, connection_id=connection.id)) == record
    assert await repo.latest(principal_id=other.id, connection_id=connection.id) is None
