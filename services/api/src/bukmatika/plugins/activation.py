from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.plugins.connections import (
    PluginConnectionRepository,
    PluginConnectionUnavailable,
)
from bukmatika.plugins.contracts import PluginRegistry
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled


class PluginActivationDenied(PermissionError):
    """The requested plugin activation state is not currently permitted."""


class PluginActivationConnectionRequired(PluginActivationDenied):
    """A connection-backed plugin has no complete current connection."""


class PluginActivationVersionMismatch(PluginActivationDenied):
    """The installed plugin version differs from the trusted registry version."""


class PluginActivationService:
    """Own explicit plugin enable/disable transitions.

    Connection configuration never activates a plugin implicitly. Enabling a
    connection-backed plugin requires a current declared connection and every
    required secret slot, but does not imply that any particular runtime action
    is authorized.
    """

    def __init__(
        self,
        session: AsyncSession,
        registry: PluginRegistry,
        *,
        installation_key: InstallationCredentialKey,
    ) -> None:
        self._registry = registry
        self._installations = PluginInstallationRepository(session, registry)
        self._connections = PluginConnectionRepository(
            session,
            registry,
            installation_key=installation_key,
        )

    async def set_enabled(
        self,
        *,
        principal_id: UUID,
        plugin_id: str,
        enabled: bool,
    ) -> PluginInstallation:
        try:
            registration = self._registry.registration(plugin_id)
            installation = await self._installations.require(
                principal_id=principal_id,
                plugin_id=plugin_id,
                lock=True,
            )
        except (LookupError, PluginNotInstalled) as exc:
            raise PluginActivationDenied("Plugin activation is unavailable") from exc

        if not enabled:
            return await self._installations.set_enabled(
                principal_id=principal_id,
                plugin_id=plugin_id,
                enabled=False,
            )

        if installation.installed_version != registration.manifest.version:
            raise PluginActivationVersionMismatch("Plugin installation version requires review")
        if registration.manifest.requires_connection:
            try:
                await self._connections.require_configured(
                    principal_id=principal_id,
                    plugin_id=plugin_id,
                )
            except PluginConnectionUnavailable as exc:
                raise PluginActivationConnectionRequired(
                    "Plugin connection requires configuration"
                ) from exc

        return await self._installations.set_enabled(
            principal_id=principal_id,
            plugin_id=plugin_id,
            enabled=True,
        )
