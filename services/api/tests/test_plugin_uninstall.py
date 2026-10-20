from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.persistence.secret_models import EncryptedSecret
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled


async def test_uninstall_removes_owned_secrets_but_not_other_principals(
    session: AsyncSession,
) -> None:
    one = Principal(kind="local", external_subject=f"uninstall-{uuid4()}")
    two = Principal(kind="local", external_subject=f"uninstall-{uuid4()}")
    session.add_all([one, two])
    await session.flush()
    registry = build_builtin_plugin_registry()
    plugin_id = registry.manifests()[0].plugin_id
    repo = PluginInstallationRepository(session, registry)
    installed_one = await repo.install(principal_id=one.id, plugin_id=plugin_id)
    installed_two = await repo.install(principal_id=two.id, plugin_id=plugin_id)
    for principal, installation in [(one, installed_one), (two, installed_two)]:
        session.add(
            EncryptedSecret(
                id=uuid4(),
                principal_id=principal.id,
                owner_kind="plugin_installation",
                owner_id=installation.id,
                slot="api_key",
                ciphertext=b"fixture",
                nonce=b"nonce",
                key_version=1,
                aad_scheme="owner_slot_v1",
            )
        )
    await session.flush()

    await repo.uninstall(principal_id=one.id, plugin_id=plugin_id)
    with pytest.raises(PluginNotInstalled):
        await repo.require(principal_id=one.id, plugin_id=plugin_id)
    assert (await repo.require(principal_id=two.id, plugin_id=plugin_id)).id == installed_two.id
    secrets = (await session.scalars(select(EncryptedSecret))).all()
    assert len(secrets) == 1
    assert secrets[0].principal_id == two.id
    assert (await session.scalars(select(PluginInstallation))).all() == [installed_two]


async def test_uninstall_requires_principal_ownership(session: AsyncSession) -> None:
    one = Principal(kind="local", external_subject=f"uninstall-{uuid4()}")
    two = Principal(kind="local", external_subject=f"uninstall-{uuid4()}")
    session.add_all([one, two])
    await session.flush()
    repo = PluginInstallationRepository(session, build_builtin_plugin_registry())
    plugin_id = build_builtin_plugin_registry().manifests()[0].plugin_id
    await repo.install(principal_id=one.id, plugin_id=plugin_id)
    with pytest.raises(PluginNotInstalled):
        await repo.uninstall(principal_id=two.id, plugin_id=plugin_id)
    assert (await repo.require(principal_id=one.id, plugin_id=plugin_id)).status == "disabled"
