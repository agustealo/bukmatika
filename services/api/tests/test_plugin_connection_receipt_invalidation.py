from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.persistence.plugin_health_models import PluginHealthReceipt
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.connections import PluginConnectionRepository
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.installations import PluginInstallationRepository


async def test_reconfigure_invalidates_old_health_without_cross_principal_deletion(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principals = [
        Principal(kind="local", external_subject=f"reconfigure-{uuid4()}")
        for _ in range(2)
    ]
    session.add_all(principals)
    await session.flush()
    registry = build_builtin_plugin_registry()
    installations = PluginInstallationRepository(session, registry)
    connections = PluginConnectionRepository(
        session, registry, installation_key=InstallationCredentialKey(tmp_path / "key")
    )
    health = PluginHealthReceiptRepository(session)
    configured = []
    for principal in principals:
        await installations.install(principal_id=principal.id, plugin_id="wordpress")
        configured.append(
            await connections.configure(
                principal_id=principal.id,
                plugin_id="wordpress",
                mode_id="rest",
                values={
                    "site_url": "https://example.com",
                    "username": "editor",
                    "application_password": "first-secret",
                },
            )
        )

    old_receipt = await health.record(
        principal_id=principals[0].id,
        connection_id=configured[0].id,
        state=ConnectionHealth.VERIFIED,
        evidence_code="PUBLIC_REST_TYPES_200",
        checked_at=datetime.now(UTC),
    )
    other_receipt = await health.record(
        principal_id=principals[1].id,
        connection_id=configured[1].id,
        state=ConnectionHealth.UNAVAILABLE,
        evidence_code="TRANSPORT_UNAVAILABLE",
        checked_at=datetime.now(UTC),
    )

    rotated = await connections.configure(
        principal_id=principals[0].id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "editor",
            "application_password": "rotated-secret",
        },
    )
    assert rotated.id == configured[0].id
    assert await health.latest(
        principal_id=principals[0].id,
        connection_id=rotated.id,
    ) is None
    assert (
        await health.latest(
            principal_id=principals[1].id,
            connection_id=configured[1].id,
        )
        == other_receipt
    )
    assert old_receipt not in (await session.scalars(select(PluginHealthReceipt))).all()

    await health.record(
        principal_id=principals[0].id,
        connection_id=rotated.id,
        state=ConnectionHealth.VERIFIED,
        evidence_code="PUBLIC_REST_TYPES_200",
        checked_at=datetime.now(UTC),
    )
    await connections.configure(
        principal_id=principals[0].id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://new-example.com",
            "username": "editor",
            "application_password": "third-secret",
        },
    )
    assert await health.latest(
        principal_id=principals[0].id,
        connection_id=rotated.id,
    ) is None
    assert (await session.scalars(select(PluginHealthReceipt))).all() == [other_receipt]
