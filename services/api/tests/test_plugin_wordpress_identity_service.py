from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.connections import PluginConnectionRepository
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.installations import PluginInstallationRepository
from bukmatika.plugins.wordpress_identity_probe import WordPressIdentityObservation
from bukmatika.plugins.wordpress_identity_service import verify_wordpress_identity


async def test_identity_service_reveals_only_to_internal_probe_and_records_evidence(
    session: AsyncSession, tmp_path: Path
) -> None:
    owner = Principal(kind="local", external_subject=f"wp-auth-{uuid4()}")
    other = Principal(kind="local", external_subject=f"wp-auth-{uuid4()}")
    session.add_all([owner, other])
    await session.flush()
    registry = build_builtin_plugin_registry()
    await PluginInstallationRepository(session, registry).install(
        principal_id=owner.id, plugin_id="wordpress"
    )
    key = InstallationCredentialKey(tmp_path / "auth-key")
    connection = await PluginConnectionRepository(
        session, registry, installation_key=key
    ).configure(
        principal_id=owner.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "author",
            "application_password": "private-app-password",
        },
    )
    with patch(
        "bukmatika.plugins.wordpress_identity_service.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=WordPressIdentityObservation(
            ConnectionHealth.VERIFIED, "AUTH_IDENTITY_200"
        ),
    ) as probe:
        with pytest.raises(LookupError):
            await verify_wordpress_identity(
                session,
                principal_id=other.id,
                connection_id=connection.id,
                installation_key=key,
            )
        probe.assert_not_awaited()
        result = await verify_wordpress_identity(
            session,
            principal_id=owner.id,
            connection_id=connection.id,
            installation_key=key,
        )
    assert probe.await_args.args[1:] == (
        "https://example.com",
        "author",
        "private-app-password",
    )
    assert result.credentials_verified
    receipt = await PluginHealthReceiptRepository(session).latest(
        principal_id=owner.id, connection_id=connection.id
    )
    assert receipt is not None
    assert receipt.evidence_code == "AUTH_IDENTITY_200"
    assert await PluginHealthReceiptRepository(session).latest(
        principal_id=other.id, connection_id=connection.id
    ) is None
