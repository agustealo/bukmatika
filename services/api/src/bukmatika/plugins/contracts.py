from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import httpx

from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveryAdapter


class PluginKind(StrEnum):
    DISCOVERY_SOURCE = "discovery_source"
    AI_PROVIDER = "ai_provider"
    PUBLISHING = "publishing"
    CONTENT_REPOSITORY = "content_repository"
    EXPORT = "export"


class PluginCapability(StrEnum):
    DISCOVERY_SEARCH = "discovery.search"
    ASSET_ACQUISITION = "asset.acquire"
    CONTENT_READ = "content.read"
    CONTENT_PUBLISH = "content.publish"
    AI_GENERATION = "ai.generate"
    AI_EMBEDDINGS = "ai.embed"
    EXPORT = "export"


DiscoveryPluginFactory = Callable[[httpx.AsyncClient, Settings], DiscoveryAdapter]


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    display_name: str
    version: str
    kinds: frozenset[PluginKind]
    capabilities: frozenset[PluginCapability]
    connection_schema_version: int = 1
    requires_connection: bool = False

    def __post_init__(self) -> None:
        plugin_id = self.plugin_id.strip()
        if not plugin_id:
            raise ValueError("plugin_id must not be empty")
        if plugin_id != self.plugin_id:
            raise ValueError("plugin_id must not contain surrounding whitespace")
        if not self.display_name.strip():
            raise ValueError("plugin display_name must not be empty")
        if not self.version.strip():
            raise ValueError("plugin version must not be empty")
        if self.connection_schema_version < 1:
            raise ValueError("connection_schema_version must be positive")
        if not self.kinds:
            raise ValueError("plugin must declare at least one kind")
        if not self.capabilities:
            raise ValueError("plugin must declare at least one capability")


@dataclass(frozen=True, slots=True)
class PluginRegistration:
    manifest: PluginManifest
    discovery_factory: DiscoveryPluginFactory | None = None

    def __post_init__(self) -> None:
        has_discovery = PluginCapability.DISCOVERY_SEARCH in self.manifest.capabilities
        if has_discovery != (self.discovery_factory is not None):
            raise ValueError(
                "discovery.search capability and discovery factory must be declared together"
            )


class PluginRegistry:
    """Trusted in-process registry for installed plugin implementations.

    This registry owns executable plugin identity and capability discovery. Durable
    install/disable/uninstall state is intentionally a separate persistence concern.
    User-supplied module paths or executable code are never loaded here.
    """

    def __init__(self, registrations: tuple[PluginRegistration, ...]) -> None:
        by_id: dict[str, PluginRegistration] = {}
        for registration in registrations:
            plugin_id = registration.manifest.plugin_id
            if plugin_id in by_id:
                raise ValueError(f"duplicate plugin id: {plugin_id}")
            by_id[plugin_id] = registration
        self._by_id = by_id

    def manifests(self) -> tuple[PluginManifest, ...]:
        return tuple(
            registration.manifest
            for registration in sorted(
                self._by_id.values(),
                key=lambda item: item.manifest.plugin_id,
            )
        )

    def registration(self, plugin_id: str) -> PluginRegistration:
        try:
            return self._by_id[plugin_id]
        except KeyError as exc:
            raise LookupError(f"plugin is not registered: {plugin_id}") from exc

    def with_capability(
        self,
        capability: PluginCapability,
    ) -> tuple[PluginRegistration, ...]:
        return tuple(
            registration
            for registration in sorted(
                self._by_id.values(),
                key=lambda item: item.manifest.plugin_id,
            )
            if capability in registration.manifest.capabilities
        )

    def discovery_registrations(self) -> tuple[PluginRegistration, ...]:
        registrations = self.with_capability(PluginCapability.DISCOVERY_SEARCH)
        for registration in registrations:
            if registration.discovery_factory is None:
                raise RuntimeError(
                    f"discovery plugin has no factory: {registration.manifest.plugin_id}"
                )
        return registrations
