from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.persistence.secret_models import EncryptedSecret
from bukmatika.plugins.activation import (
    PluginActivationConnectionRequired,
    PluginActivationDenied,
    PluginActivationService,
    PluginActivationVersionMismatch,
)
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connections import PluginConnectionRepository
from bukmatika.plugins.contracts import PluginCapability
from bukmatika.plugins.execution import PluginExecutionPolicy
from bukmatika.plugins.installations import PluginInstallationRepository


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject=f"plugin-activation-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


def _wordpress_values(password: str = "private-application-password") -> dict[str, str]:
    return {
        "site_url": "https://example.com",
        "username": "editor",
        "application_password": password,
    }


async def test_connection_backed_plugin_requires_explicit_complete_activation(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    registry = build_builtin_plugin_registry()
    installations = PluginInstallationRepository(session, registry)
    installed = await installations.install(principal_id=principal.id, plugin_id="wordpress")
    assert installed.status == "disabled"

    key = InstallationCredentialKey(tmp_path / "key")
    activation = PluginActivationService(session, registry, installation_key=key)
    with pytest.raises(PluginActivationConnectionRequired):
        await activation.set_enabled(
            principal_id=principal.id,
            plugin_id="wordpress",
            enabled=True,
        )
    assert (
        await installations.require(principal_id=principal.id, plugin_id="wordpress")
    ).status == "disabled"

    connections = PluginConnectionRepository(session, registry, installation_key=key)
    connection = await connections.configure(
        principal_id=principal.id,
        plugin_id="wordpress",
        mode_id="rest",
        values=_wordpress_values(),
    )
    assert connection.settings == {
        "site_url": "https://example.com",
        "username": "editor",
    }
    assert (
        await installations.require(principal_id=principal.id, plugin_id="wordpress")
    ).status == "disabled"

    enabled = await activation.set_enabled(
        principal_id=principal.id,
        plugin_id="wordpress",
        enabled=True,
    )
    assert enabled.status == "enabled"
    registration = await PluginExecutionPolicy(session, registry).authorize(
        principal_id=principal.id,
        plugin_id="wordpress",
        capability=PluginCapability.CONTENT_PUBLISH,
    )
    assert registration.manifest.plugin_id == "wordpress"

    await session.execute(
        delete(EncryptedSecret).where(
            EncryptedSecret.principal_id == principal.id,
            EncryptedSecret.owner_kind == "plugin_connection",
            EncryptedSecret.owner_id == connection.id,
        )
    )
    disabled = await activation.set_enabled(
        principal_id=principal.id,
        plugin_id="wordpress",
        enabled=False,
    )
    assert disabled.status == "disabled"
    with pytest.raises(PluginActivationConnectionRequired):
        await activation.set_enabled(
            principal_id=principal.id,
            plugin_id="wordpress",
            enabled=True,
        )


async def test_activation_is_principal_scoped_and_version_fenced(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner = await _principal(session)
    outsider = await _principal(session)
    registry = build_builtin_plugin_registry()
    installations = PluginInstallationRepository(session, registry)
    row = await installations.install(principal_id=owner.id, plugin_id="wordpress")
    key = InstallationCredentialKey(tmp_path / "key")
    await PluginConnectionRepository(session, registry, installation_key=key).configure(
        principal_id=owner.id,
        plugin_id="wordpress",
        mode_id="rest",
        values=_wordpress_values(),
    )
    activation = PluginActivationService(session, registry, installation_key=key)

    with pytest.raises(PluginActivationDenied):
        await activation.set_enabled(
            principal_id=outsider.id,
            plugin_id="wordpress",
            enabled=True,
        )

    row.installed_version = "0.0.0"
    await session.flush()
    with pytest.raises(PluginActivationVersionMismatch):
        await activation.set_enabled(
            principal_id=owner.id,
            plugin_id="wordpress",
            enabled=True,
        )
    assert row.status == "disabled"
