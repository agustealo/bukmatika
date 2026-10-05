import httpx
import pytest

from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveryAdapter
from bukmatika.plugins import (
    PluginCapability,
    PluginKind,
    PluginManifest,
    PluginRegistration,
    PluginRegistry,
    build_builtin_plugin_registry,
)


class _DiscoveryAdapter:
    name = "synthetic"

    async def search(self, intent):  # type: ignore[no-untyped-def]
        del intent
        return []


def _factory(client: httpx.AsyncClient, settings: Settings) -> DiscoveryAdapter:
    del client, settings
    return _DiscoveryAdapter()


def _manifest(plugin_id: str = "synthetic") -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        display_name="Synthetic",
        version="1.0.0",
        kinds=frozenset({PluginKind.DISCOVERY_SOURCE}),
        capabilities=frozenset({PluginCapability.DISCOVERY_SEARCH}),
    )


def test_plugin_registry_rejects_duplicate_plugin_ids() -> None:
    registration = PluginRegistration(
        manifest=_manifest(),
        discovery_factory=_factory,
    )
    with pytest.raises(ValueError, match="duplicate plugin id"):
        PluginRegistry((registration, registration))


def test_discovery_capability_requires_discovery_factory() -> None:
    with pytest.raises(ValueError, match="discovery.search capability"):
        PluginRegistration(manifest=_manifest())


def test_discovery_factory_requires_discovery_capability() -> None:
    manifest = PluginManifest(
        plugin_id="publisher",
        display_name="Publisher",
        version="1.0.0",
        kinds=frozenset({PluginKind.PUBLISHING}),
        capabilities=frozenset({PluginCapability.CONTENT_PUBLISH}),
    )
    with pytest.raises(ValueError, match="discovery.search capability"):
        PluginRegistration(
            manifest=manifest,
            discovery_factory=_factory,
        )


def test_unknown_plugin_id_fails_closed() -> None:
    registry = PluginRegistry(())
    with pytest.raises(LookupError, match="plugin is not registered"):
        registry.registration("missing")


async def test_bundled_discovery_plugins_match_manifest_identity() -> None:
    registry = build_builtin_plugin_registry()
    registrations = registry.discovery_registrations()
    expected = {
        "internet_archive",
        "library_of_congress",
        "open_library",
        "project_gutenberg",
    }
    assert {item.manifest.plugin_id for item in registrations} == expected

    settings = Settings()
    async with httpx.AsyncClient() as client:
        for registration in registrations:
            assert registration.discovery_factory is not None
            adapter = registration.discovery_factory(client, settings)
            assert adapter.name == registration.manifest.plugin_id
            assert PluginKind.DISCOVERY_SOURCE in registration.manifest.kinds
            assert PluginCapability.DISCOVERY_SEARCH in registration.manifest.capabilities
            assert registration.manifest.requires_connection is False
