from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import DatabaseSecretStore
from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.plugins.contracts import PluginRegistry


class PluginNotInstalled(LookupError):
    pass


class PluginDisabled(PermissionError):
    pass


class PluginInstallationService:
    """Principal-scoped durable plugin lifecycle. No untrusted code loading."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: PluginRegistry,
        secrets: DatabaseSecretStore,
    ) -> None:
        self._session = session
        self._registry = registry
        self._secrets = secrets

    async def list_installed(self, *, principal_id: UUID) -> list[PluginInstallation]:
        result = await self._session.scalars(
            select(PluginInstallation)
            .where(PluginInstallation.principal_id == principal_id)
            .order_by(PluginInstallation.plugin_id)
        )
        return list(result)

    async def _installed(
        self, *, principal_id: UUID, plugin_id: str, lock: bool = False
    ) -> PluginInstallation:
        query = select(PluginInstallation).where(
            PluginInstallation.principal_id == principal_id,
            PluginInstallation.plugin_id == plugin_id,
        )
        if lock:
            query = query.with_for_update()
        row = await self._session.scalar(query)
        if row is None:
            raise PluginNotInstalled("Plugin is not installed for this principal")
        return row

    async def install(self, *, principal_id: UUID, plugin_id: str) -> PluginInstallation:
        manifest = self._registry.registration(plugin_id).manifest
        existing = await self._session.scalar(
            select(PluginInstallation).where(
                PluginInstallation.principal_id == principal_id,
                PluginInstallation.plugin_id == plugin_id,
            )
        )
        if existing is not None:
            return existing
        row = PluginInstallation(
            principal_id=principal_id,
            plugin_id=manifest.plugin_id,
            installed_version=manifest.version,
            connection_schema_version=manifest.connection_schema_version,
            status="disabled" if manifest.requires_connection else "enabled",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_enabled(
        self, *, principal_id: UUID, plugin_id: str, enabled: bool
    ) -> PluginInstallation:
        manifest = self._registry.registration(plugin_id).manifest
        row = await self._installed(principal_id=principal_id, plugin_id=plugin_id, lock=True)
        if enabled and manifest.requires_connection:
            raise PluginDisabled("Plugin connection must be validated before enabling")
        if enabled and (
            row.installed_version != manifest.version
            or row.connection_schema_version != manifest.connection_schema_version
        ):
            raise PluginDisabled("Installed plugin version requires an explicit upgrade")
        row.status = "enabled" if enabled else "disabled"
        await self._session.flush()
        return row

    async def require_enabled(
        self, *, principal_id: UUID, plugin_id: str
    ) -> PluginInstallation:
        manifest = self._registry.registration(plugin_id).manifest
        row = await self._installed(principal_id=principal_id, plugin_id=plugin_id)
        if (
            row.status != "enabled"
            or row.installed_version != manifest.version
            or row.connection_schema_version != manifest.connection_schema_version
        ):
            raise PluginDisabled("Plugin is disabled or requires an upgrade")
        return row

    async def uninstall(self, *, principal_id: UUID, plugin_id: str) -> None:
        row = await self._installed(principal_id=principal_id, plugin_id=plugin_id, lock=True)
        # Disable execution and destroy all secrets owned by this installation.
        row.status = "disabled"
        await self._session.flush()
        await self._secrets.delete_owner(
            principal_id=principal_id,
            owner_kind="plugin_installation",
            owner_id=row.id,
        )
        await self._session.delete(row)
        await self._session.flush()
