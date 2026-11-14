from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.plugins.contracts import PluginCapability, PluginRegistration, PluginRegistry
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled


class PluginExecutionDenied(PermissionError):
    """The principal has no active grant for this plugin capability."""


class PluginVersionMismatch(PluginExecutionDenied):
    """The installed plugin version differs from the trusted executable version."""


class PluginExecutionPolicy:
    """One guarded entry point for principal-owned plugin invocation.

    Built-in public discovery is intentionally separate: existing anonymous
    /v1/discover behavior is not governed by principal installation rows.
    """

    def __init__(self, session: AsyncSession, registry: PluginRegistry) -> None:
        self._installations = PluginInstallationRepository(session, registry)
        self._registry = registry

    async def authorize(
        self,
        *,
        principal_id: UUID,
        plugin_id: str,
        capability: PluginCapability,
    ) -> PluginRegistration:
        # Reject untrusted plugin identity and capability before touching execution.
        try:
            registration = self._registry.registration(plugin_id)
        except LookupError as exc:
            raise PluginExecutionDenied("Plugin execution is unavailable") from exc
        if capability not in registration.manifest.capabilities:
            raise PluginExecutionDenied("Plugin capability is unavailable")
        try:
            installation = await self._installations.require(
                principal_id=principal_id,
                plugin_id=plugin_id,
                enabled_only=True,
            )
        except PluginNotInstalled as exc:
            raise PluginExecutionDenied("Plugin execution is unavailable") from exc
        if installation.installed_version != registration.manifest.version:
            raise PluginVersionMismatch("Plugin installation version requires review")
        return registration
