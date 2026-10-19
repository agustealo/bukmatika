from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.contracts import PluginCapability
from bukmatika.plugins.execution import (
    PluginExecutionDenied,
    PluginExecutionPolicy,
    PluginVersionMismatch,
)
from bukmatika.plugins.installations import PluginInstallationRepository


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject=f"plugin-execution-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


async def test_plugin_requires_enabled_installation_for_execution(session: AsyncSession) -> None:
    principal = await _principal(session)
    registry = build_builtin_plugin_registry()
    plugin_id = registry.manifests()[0].plugin_id
    policy = PluginExecutionPolicy(session, registry)
    installs = PluginInstallationRepository(session, registry)

    with pytest.raises(PluginExecutionDenied):
        await policy.authorize(
            principal_id=principal.id,
            plugin_id=plugin_id,
            capability=PluginCapability.DISCOVERY_SEARCH,
        )
    await installs.install(principal_id=principal.id, plugin_id=plugin_id)
    with pytest.raises(PluginExecutionDenied):
        await policy.authorize(
            principal_id=principal.id,
            plugin_id=plugin_id,
            capability=PluginCapability.DISCOVERY_SEARCH,
        )

    await installs.set_enabled(principal_id=principal.id, plugin_id=plugin_id, enabled=True)
    registration = await policy.authorize(
        principal_id=principal.id,
        plugin_id=plugin_id,
        capability=PluginCapability.DISCOVERY_SEARCH,
    )
    assert registration.manifest.plugin_id == plugin_id

    await installs.set_enabled(principal_id=principal.id, plugin_id=plugin_id, enabled=False)
    with pytest.raises(PluginExecutionDenied):
        await policy.authorize(
            principal_id=principal.id,
            plugin_id=plugin_id,
            capability=PluginCapability.DISCOVERY_SEARCH,
        )


async def test_plugin_execution_is_principal_isolated(session: AsyncSession) -> None:
    owner = await _principal(session)
    other = await _principal(session)
    registry = build_builtin_plugin_registry()
    plugin_id = registry.manifests()[0].plugin_id
    installs = PluginInstallationRepository(session, registry)
    await installs.install(principal_id=owner.id, plugin_id=plugin_id)
    await installs.set_enabled(principal_id=owner.id, plugin_id=plugin_id, enabled=True)

    with pytest.raises(PluginExecutionDenied):
        await PluginExecutionPolicy(session, registry).authorize(
            principal_id=other.id,
            plugin_id=plugin_id,
            capability=PluginCapability.DISCOVERY_SEARCH,
        )


async def test_unknown_plugin_and_undeclared_capability_rejected(
    session: AsyncSession,
) -> None:
    principal = await _principal(session)
    registry = build_builtin_plugin_registry()
    policy = PluginExecutionPolicy(session, registry)
    with pytest.raises(PluginExecutionDenied):
        await policy.authorize(
            principal_id=principal.id,
            plugin_id="arbitrary.python.module",
            capability=PluginCapability.CONTENT_PUBLISH,
        )
    plugin_id = registry.manifests()[0].plugin_id
    installs = PluginInstallationRepository(session, registry)
    await installs.install(principal_id=principal.id, plugin_id=plugin_id)
    await installs.set_enabled(principal_id=principal.id, plugin_id=plugin_id, enabled=True)
    with pytest.raises(PluginExecutionDenied):
        await policy.authorize(
            principal_id=principal.id,
            plugin_id=plugin_id,
            capability=PluginCapability.CONTENT_PUBLISH,
        )


async def test_version_drift_blocks_execution(session: AsyncSession) -> None:
    principal = await _principal(session)
    registry = build_builtin_plugin_registry()
    plugin_id = registry.manifests()[0].plugin_id
    installs = PluginInstallationRepository(session, registry)
    installation = await installs.install(principal_id=principal.id, plugin_id=plugin_id)
    await installs.set_enabled(principal_id=principal.id, plugin_id=plugin_id, enabled=True)
    installation.installed_version = "0.0.0"
    await session.flush()
    with pytest.raises(PluginVersionMismatch):
        await PluginExecutionPolicy(session, registry).authorize(
            principal_id=principal.id,
            plugin_id=plugin_id,
            capability=PluginCapability.DISCOVERY_SEARCH,
        )
