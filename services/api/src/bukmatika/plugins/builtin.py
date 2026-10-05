from bukmatika.discovery.gutenberg import ProjectGutenbergAdapter
from bukmatika.discovery.internet_archive import InternetArchiveAdapter
from bukmatika.discovery.library_of_congress import LibraryOfCongressAdapter
from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.plugins.contracts import (
    PluginCapability,
    PluginKind,
    PluginManifest,
    PluginRegistration,
    PluginRegistry,
)


def build_builtin_plugin_registry() -> PluginRegistry:
    """Return trusted integrations shipped with this Bukmatika build."""

    discovery_kind = frozenset({PluginKind.DISCOVERY_SOURCE})
    discovery_capabilities = frozenset({PluginCapability.DISCOVERY_SEARCH})
    return PluginRegistry(
        (
            PluginRegistration(
                manifest=PluginManifest(
                    plugin_id=OpenLibraryAdapter.name,
                    display_name="Open Library",
                    version="1.0.0",
                    kinds=discovery_kind,
                    capabilities=discovery_capabilities,
                    requires_connection=False,
                ),
                discovery_factory=OpenLibraryAdapter,
            ),
            PluginRegistration(
                manifest=PluginManifest(
                    plugin_id=InternetArchiveAdapter.name,
                    display_name="Internet Archive",
                    version="1.0.0",
                    kinds=discovery_kind,
                    capabilities=discovery_capabilities,
                    requires_connection=False,
                ),
                discovery_factory=InternetArchiveAdapter,
            ),
            PluginRegistration(
                manifest=PluginManifest(
                    plugin_id=ProjectGutenbergAdapter.name,
                    display_name="Project Gutenberg",
                    version="1.0.0",
                    kinds=discovery_kind,
                    capabilities=discovery_capabilities,
                    requires_connection=False,
                ),
                discovery_factory=ProjectGutenbergAdapter,
            ),
            PluginRegistration(
                manifest=PluginManifest(
                    plugin_id=LibraryOfCongressAdapter.name,
                    display_name="Library of Congress",
                    version="1.0.0",
                    kinds=discovery_kind,
                    capabilities=discovery_capabilities,
                    requires_connection=False,
                ),
                discovery_factory=LibraryOfCongressAdapter,
            ),
        )
    )
