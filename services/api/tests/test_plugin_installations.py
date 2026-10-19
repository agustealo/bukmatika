from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject=f"plugin-install-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


async def test_installation_defaults_disabled_and_persists_across_repositories(
    session: AsyncSession,
) -> None:
    owner = await _principal(session)
    registry = build_builtin_plugin_registry()
    plugin_id = registry.manifests()[0].plugin_id
    repository = PluginInstallationRepository(session, registry)
    installed = await repository.install(principal_id=owner.id, plugin_id=plugin_id)
    assert installed.status == "disabled"
    assert installed.installed_version == registry.registration(plugin_id).manifest.version
    again = PluginInstallationRepository(session, registry)
    assert (await again.require(principal_id=owner.id, plugin_id=plugin_id)).id == installed.id
    with pytest.raises(PluginNotInstalled):
        await again.require(principal_id=owner.id, plugin_id=plugin_id, enabled_only=True)
    enabled = await again.set_enabled(principal_id=owner.id, plugin_id=plugin_id, enabled=True)
    assert enabled.status == "enabled"
    await again.set_enabled(principal_id=owner.id, plugin_id=plugin_id, enabled=False)
    with pytest.raises(PluginNotInstalled):
        await again.require(principal_id=owner.id, plugin_id=plugin_id, enabled_only=True)


async def test_cross_principal_installation_access_denied(session: AsyncSession) -> None:
    owner = await _principal(session)
    other = await _principal(session)
    registry = build_builtin_plugin_registry()
    plugin_id = registry.manifests()[0].plugin_id
    repository = PluginInstallationRepository(session, registry)
    await repository.install(principal_id=owner.id, plugin_id=plugin_id)
    with pytest.raises(PluginNotInstalled):
        await repository.set_enabled(principal_id=other.id, plugin_id=plugin_id, enabled=True)
    assert await repository.list(principal_id=other.id) == []


async def test_unknown_plugin_rejected_before_insert(session: AsyncSession) -> None:
    owner = await _principal(session)
    repository = PluginInstallationRepository(session, build_builtin_plugin_registry())
    with pytest.raises(LookupError):
        await repository.install(principal_id=owner.id, plugin_id="untrusted.module.path")
    assert await repository.list(principal_id=owner.id) == []
