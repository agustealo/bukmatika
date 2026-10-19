from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import (
    CredentialUnavailable,
    DatabaseSecretStore,
    InstallationCredentialKey,
    SecretOwner,
)
from bukmatika.persistence.models import Principal
from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.installations import (
    PluginDisabled,
    PluginInstallationService,
    PluginNotInstalled,
)


async def _service(
    session: AsyncSession, key_path: Path
) -> tuple[Principal, PluginInstallationService, DatabaseSecretStore]:
    principal = Principal(kind="local", external_subject=f"plugin-install-{uuid4()}")
    session.add(principal)
    await session.flush()
    secrets = DatabaseSecretStore(
        session, installation_key=InstallationCredentialKey(key_path)
    )
    return principal, PluginInstallationService(
        session, registry=build_builtin_plugin_registry(), secrets=secrets
    ), secrets


async def test_install_disable_enable_and_uninstall_are_durable(
    session: AsyncSession, tmp_path: Path
) -> None:
    principal, service, secrets = await _service(session, tmp_path / "credentials.key")
    plugin_id = "open_library"

    with pytest.raises(PluginNotInstalled):
        await service.require_enabled(principal_id=principal.id, plugin_id=plugin_id)

    installation = await service.install(principal_id=principal.id, plugin_id=plugin_id)
    assert installation.status == "enabled"
    assert (await service.install(principal_id=principal.id, plugin_id=plugin_id)).id == installation.id
    assert (await service.require_enabled(principal_id=principal.id, plugin_id=plugin_id)).id == installation.id

    await service.set_enabled(principal_id=principal.id, plugin_id=plugin_id, enabled=False)
    with pytest.raises(PluginDisabled):
        await service.require_enabled(principal_id=principal.id, plugin_id=plugin_id)

    await service.set_enabled(principal_id=principal.id, plugin_id=plugin_id, enabled=True)
    assert (await service.require_enabled(principal_id=principal.id, plugin_id=plugin_id)).id == installation.id

    owner = SecretOwner(
        principal_id=principal.id,
        owner_kind="plugin_installation",
        owner_id=installation.id,
        slot="token",
    )
    ref = await secrets.replace(owner=owner, secret="uninstall-destroys-me")
    await service.uninstall(principal_id=principal.id, plugin_id=plugin_id)

    assert await session.scalar(
        select(PluginInstallation).where(PluginInstallation.id == installation.id)
    ) is None
    with pytest.raises(CredentialUnavailable):
        await secrets.reveal(owner=owner, reference=ref)
    with pytest.raises(PluginNotInstalled):
        await service.require_enabled(principal_id=principal.id, plugin_id=plugin_id)


async def test_principal_scoping_and_unknown_plugin_fail_closed(
    session: AsyncSession, tmp_path: Path
) -> None:
    principal, service, _ = await _service(session, tmp_path / "credentials.key")
    other = Principal(kind="local", external_subject=f"plugin-other-{uuid4()}")
    session.add(other)
    await session.flush()
    await service.install(principal_id=principal.id, plugin_id="open_library")

    assert await service.list_installed(principal_id=other.id) == []
    with pytest.raises(PluginNotInstalled):
        await service.set_enabled(principal_id=other.id, plugin_id="open_library", enabled=False)
    with pytest.raises(LookupError):
        await service.install(principal_id=principal.id, plugin_id="untrusted.module.path")
