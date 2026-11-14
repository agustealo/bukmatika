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
from bukmatika.plugins.wordpress_capability_probe import (
    WORDPRESS_CAPABILITY_KEYS,
    WordPressCapabilityObservation,
)
from bukmatika.plugins.wordpress_capability_service import verify_wordpress_capabilities


async def test_capability_service_is_owner_scoped_and_records_only_evidence(
    session: AsyncSession, tmp_path: Path
) -> None:
    owner = Principal(kind="local", external_subject=f"wp-cap-{uuid4()}")
    outsider = Principal(kind="local", external_subject=f"wp-cap-{uuid4()}")
    session.add_all([owner, outsider])
    await session.flush()
    registry = build_builtin_plugin_registry()
    await PluginInstallationRepository(session, registry).install(
        principal_id=owner.id, plugin_id="wordpress"
    )
    key = InstallationCredentialKey(tmp_path / "key")
    connection = await PluginConnectionRepository(
        session, registry, installation_key=key
    ).configure(
        principal_id=owner.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "author",
            "application_password": "secret-app-password",
        },
    )
    with (
        patch(
            "bukmatika.plugins.wordpress_capability_service.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as network,
        pytest.raises(LookupError),
    ):
        await verify_wordpress_capabilities(
            session,
            principal_id=outsider.id,
            connection_id=connection.id,
            installation_key=key,
        )
    network.assert_not_awaited()

    observation = WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        {key: key == "edit_posts" for key in WORDPRESS_CAPABILITY_KEYS},
    )
    with patch(
        "bukmatika.plugins.wordpress_capability_service.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=observation,
    ) as network:
        result = await verify_wordpress_capabilities(
            session,
            principal_id=owner.id,
            connection_id=connection.id,
            installation_key=key,
        )
    assert result.capabilities["edit_posts"]
    assert not result.capabilities["publish_posts"]
    assert network.await_args.args[1:] == (
        "https://example.com",
        "author",
        "secret-app-password",
    )
    receipt = await PluginHealthReceiptRepository(session).latest(
        principal_id=owner.id, connection_id=connection.id
    )
    assert receipt is not None
    assert receipt.evidence_code == "AUTH_CAPABILITIES_200"
    assert "secret-app-password" not in repr(result)


async def test_unavailable_probe_cannot_return_granted_capabilities(
    session: AsyncSession, tmp_path: Path
) -> None:
    owner = Principal(kind="local", external_subject=f"wp-cap-{uuid4()}")
    session.add(owner)
    await session.flush()
    registry = build_builtin_plugin_registry()
    await PluginInstallationRepository(session, registry).install(
        principal_id=owner.id, plugin_id="wordpress"
    )
    key = InstallationCredentialKey(tmp_path / "key")
    connection = await PluginConnectionRepository(
        session, registry, installation_key=key
    ).configure(
        principal_id=owner.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "author",
            "application_password": "secret",
        },
    )
    with patch(
        "bukmatika.plugins.wordpress_capability_service.asyncio.to_thread",
        new_callable=AsyncMock,
        return_value=WordPressCapabilityObservation(
            ConnectionHealth.UNAVAILABLE,
            "AUTH_CAPABILITY_REJECTED",
            {key: key == "publish_posts" for key in WORDPRESS_CAPABILITY_KEYS},
        ),
    ), pytest.raises(ValueError, match="cannot grant"):
        await verify_wordpress_capabilities(
            session,
            principal_id=owner.id,
            connection_id=connection.id,
            installation_key=key,
        )
    assert await PluginHealthReceiptRepository(session).latest(
        principal_id=owner.id, connection_id=connection.id
    ) is None
