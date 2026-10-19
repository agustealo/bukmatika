from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.models import Principal
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins import routes
from bukmatika.plugins.routes import PluginStatusUpdate


async def _identity(session: AsyncSession) -> AuthenticatedPrincipal:
    principal = Principal(kind="local", external_subject=f"plugin-routes-{uuid4()}")
    session.add(principal)
    await session.flush()
    return AuthenticatedPrincipal(principal_id=principal.id, session_id=uuid4())


def test_plugin_routes_registered() -> None:
    assert {route.path for route in routes.router.routes} == {
        "/v1/plugins", "/v1/plugins/{plugin_id}/install", "/v1/plugins/{plugin_id}"
    }


def test_management_capabilities_are_registry_driven() -> None:
    registry = build_builtin_plugin_registry()
    for manifest in registry.manifests():
        value = routes._plugin_state(manifest, None, False)
        assert not value.installed
        assert not value.enabled
        assert value.plugin_id == manifest.plugin_id
        assert value.capabilities == sorted(cap.value for cap in manifest.capabilities)


def test_status_payload_requires_boolean() -> None:
    assert PluginStatusUpdate(enabled=True).enabled
    with pytest.raises(Exception):
        PluginStatusUpdate(enabled="sometimes")
