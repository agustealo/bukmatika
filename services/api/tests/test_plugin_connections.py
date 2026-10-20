from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.persistence.secret_models import EncryptedSecret
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connections import PluginConnectionInvalid, PluginConnectionRepository
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled


async def test_wordpress_connection_secrets_never_enter_settings(
    session: AsyncSession, tmp_path: Path
) -> None:
    principal = Principal(kind="local", external_subject=f"wp-conn-{uuid4()}")
    session.add(principal)
    await session.flush()
    registry = build_builtin_plugin_registry()
    installs = PluginInstallationRepository(session, registry)
    await installs.install(principal_id=principal.id, plugin_id="wordpress")
    connections = PluginConnectionRepository(
        session, registry, installation_key=InstallationCredentialKey(tmp_path / "key")
    )
    row = await connections.configure(
        principal_id=principal.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "editor",
            "application_password": "private-application-password",
        },
    )
    assert row.settings == {"site_url": "https://example.com", "username": "editor"}
    secrets = (await session.scalars(select(EncryptedSecret))).all()
    assert len(secrets) == 1
    assert secrets[0].owner_id == row.id
    assert secrets[0].owner_kind == "plugin_connection"
    assert b"private-application-password" not in secrets[0].ciphertext
    await installs.uninstall(principal_id=principal.id, plugin_id="wordpress")
    assert (await session.scalars(select(EncryptedSecret))).all() == []
    assert (await session.scalars(select(PluginConnection))).all() == []


async def test_connection_rejects_unknown_fields_and_cross_principal(
    session: AsyncSession, tmp_path: Path
) -> None:
    principal = Principal(kind="local", external_subject=f"wp-conn-{uuid4()}")
    other = Principal(kind="local", external_subject=f"wp-conn-{uuid4()}")
    session.add_all([principal, other])
    await session.flush()
    registry = build_builtin_plugin_registry()
    await PluginInstallationRepository(session, registry).install(
        principal_id=principal.id, plugin_id="wordpress"
    )
    connections = PluginConnectionRepository(
        session, registry, installation_key=InstallationCredentialKey(tmp_path / "key")
    )
    values = {
        "site_url": "https://example.com",
        "username": "editor",
        "application_password": "private",
    }
    with pytest.raises(PluginConnectionInvalid):
        await connections.configure(
            principal_id=principal.id,
            plugin_id="wordpress",
            mode_id="rest",
            values={**values, "unexpected": "value"},
        )
    with pytest.raises(PluginNotInstalled):
        await connections.configure(
            principal_id=other.id, plugin_id="wordpress", mode_id="rest", values=values
        )
    assert (await session.scalars(select(PluginConnection))).all() == []
