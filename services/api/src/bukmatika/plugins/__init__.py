"""Plugin manifests, registries, and bundled integration factories."""

from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.contracts import (
    DiscoveryPluginFactory,
    PluginCapability,
    PluginKind,
    PluginManifest,
    PluginRegistration,
    PluginRegistry,
)

__all__ = [
    "DiscoveryPluginFactory",
    "PluginCapability",
    "PluginKind",
    "PluginManifest",
    "PluginRegistration",
    "PluginRegistry",
    "build_builtin_plugin_registry",
]
