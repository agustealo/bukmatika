"""Read-only plugin activation must be principal-owned and rotation-safe."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connections import PluginConnectionRepository
from bukmatika.plugins.installations import PluginInstallationRepository
from bukmatika.plugins.routes import PluginStatusUpdate, update_plugin


@pytest.mark.asyncio
async def test_enabling_wordpress_requires_owned_configured_credential(
    session: AsyncSession, tmp_path: Path
) -> None:
    principal = Principal(kind="local", external_subject=f"wp-activation-{uuid4()}")
    session.add(principal)
    await session.flush()
    owner = SimpleNamespace(principal_id=principal.id)
    registry = build_builtin_plugin_registry()
    installation = await PluginInstallationRepository(session, registry).install(
        principal_id=principal.id, plugin_id="wordpress"
    )

    @asynccontextmanager
    async def scope():
        yield session

    with patch("bukmatika.plugins.routes.session_scope", scope):
        with pytest.raises(HTTPException) as missing:
            await update_plugin("wordpress", PluginStatusUpdate(enabled=True), owner)
        assert missing.value.status_code == 409
        assert installation.status == "disabled"

        credentials = PluginConnectionRepository(
            session, registry, installation_key=InstallationCredentialKey(tmp_path / "vault")
        )
        await credentials.configure(
            principal_id=principal.id,
            plugin_id="wordpress",
            mode_id="rest",
            values={
                "site_url": "https://example.com",
                "username": "editor",
                "application_password": "private-app-pass",
            },
        )
        activated = await update_plugin(
            "wordpress", PluginStatusUpdate(enabled=True), owner
        )
        assert activated.enabled
        assert installation.status == "enabled"

        # A connection edit removes active runtime approval until re-enabled.
        await credentials.configure(
            principal_id=principal.id,
            plugin_id="wordpress",
            mode_id="rest",
            values={
                "site_url": "https://example.com",
                "username": "editor",
                "application_password": "rotated-private-app-pass",
            },
        )
        assert installation.status == "disabled"


@pytest.mark.asyncio
async def test_other_principal_cannot_activate_wordpress_connection(
    session: AsyncSession, tmp_path: Path
) -> None:
    registry = build_builtin_plugin_registry()
    owners = [
        Principal(kind="local", external_subject=f"wp-isolation-{uuid4()}")
        for _ in range(2)
    ]
    session.add_all(owners)
    await session.flush()
    for principal in owners:
        await PluginInstallationRepository(session, registry).install(
            principal_id=principal.id, plugin_id="wordpress"
        )
    await PluginConnectionRepository(
        session, registry, installation_key=InstallationCredentialKey(tmp_path / "vault")
    ).configure(
        principal_id=owners[0].id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "editor",
            "application_password": "only-first-owner",
        },
    )

    @asynccontextmanager
    async def scope():
        yield session

    with (
        patch("bukmatika.plugins.routes.session_scope", scope),
        pytest.raises(HTTPException) as denied,
    ):
        await update_plugin(
            "wordpress",
            PluginStatusUpdate(enabled=True),
            SimpleNamespace(principal_id=owners[1].id),
        )
    assert denied.value.status_code == 409
