from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.plugins.contracts import PluginRegistry


class PluginNotInstalled(LookupError):
    pass


class PluginInstallationRepository:
    """Principal-scoped state transitions; never loads arbitrary plugin code."""

    def __init__(self, session: AsyncSession, registry: PluginRegistry) -> None:
        self._session = session
        self._registry = registry

    async def list(self, *, principal_id: UUID) -> list[PluginInstallation]:
        rows = await self._session.scalars(
            select(PluginInstallation)
            .where(PluginInstallation.principal_id == principal_id)
            .order_by(PluginInstallation.plugin_id)
        )
        return list(rows)

    async def require(
        self, *, principal_id: UUID, plugin_id: str, enabled_only: bool = False
    ) -> PluginInstallation:
        query = select(PluginInstallation).where(
            PluginInstallation.principal_id == principal_id,
            PluginInstallation.plugin_id == plugin_id,
        )
        if enabled_only:
            query = query.where(PluginInstallation.status == "enabled")
        row = await self._session.scalar(query)
        if row is None:
            raise PluginNotInstalled("Plugin is not installed or enabled")
        self._registry.registration(plugin_id)
        return row

    async def install(self, *, principal_id: UUID, plugin_id: str) -> PluginInstallation:
        manifest = self._registry.registration(plugin_id).manifest
        row = await self._session.scalar(
            select(PluginInstallation).where(
                PluginInstallation.principal_id == principal_id,
                PluginInstallation.plugin_id == plugin_id,
            )
        )
        if row is not None:
            return row
        row = PluginInstallation(
            principal_id=principal_id,
            plugin_id=plugin_id,
            installed_version=manifest.version,
            status="disabled",
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def set_enabled(
        self, *, principal_id: UUID, plugin_id: str, enabled: bool
    ) -> PluginInstallation:
        row = await self.require(principal_id=principal_id, plugin_id=plugin_id)
        row.status = "enabled" if enabled else "disabled"
        await self._session.flush()
        return row
