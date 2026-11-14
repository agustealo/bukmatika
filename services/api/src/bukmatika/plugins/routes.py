from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence import session_scope
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.persistence.secret_models import EncryptedSecret
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])
_registry = build_builtin_plugin_registry()


class PluginState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plugin_id: str
    display_name: str
    version: str
    installed: bool
    enabled: bool
    requires_connection: bool
    capabilities: list[str]


class PluginCatalog(BaseModel):
    plugins: list[PluginState]


class PluginStatusUpdate(BaseModel):
    enabled: bool


def _plugin_state(manifest: object, installed_version: str | None, enabled: bool) -> PluginState:
    from bukmatika.plugins.contracts import PluginManifest

    if not isinstance(manifest, PluginManifest):
        raise TypeError("Invalid plugin manifest")
    return PluginState(
        plugin_id=manifest.plugin_id,
        display_name=manifest.display_name,
        version=manifest.version,
        installed=installed_version is not None,
        enabled=enabled,
        requires_connection=manifest.requires_connection,
        capabilities=sorted(cap.value for cap in manifest.capabilities),
    )


@router.get("", response_model=PluginCatalog)
async def list_plugins(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> PluginCatalog:
    async with session_scope() as session:
        repo = PluginInstallationRepository(session, _registry)
        installations = {
            row.plugin_id: row for row in await repo.list(principal_id=identity.principal_id)
        }
        return PluginCatalog(
            plugins=[
                _plugin_state(
                    manifest,
                    installations[manifest.plugin_id].installed_version
                    if manifest.plugin_id in installations
                    else None,
                    manifest.plugin_id in installations
                    and installations[manifest.plugin_id].status == "enabled"
                    and installations[manifest.plugin_id].installed_version == manifest.version,
                )
                for manifest in _registry.manifests()
            ]
        )


@router.post(
    "/{plugin_id}/install",
    response_model=PluginState,
    status_code=status.HTTP_201_CREATED,
)
async def install_plugin(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> PluginState:
    try:
        manifest = _registry.registration(plugin_id).manifest
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"}) from exc
    async with session_scope() as session:
        repo = PluginInstallationRepository(session, _registry)
        row = await repo.install(principal_id=identity.principal_id, plugin_id=plugin_id)
        return _plugin_state(manifest, row.installed_version, row.status == "enabled")


@router.patch("/{plugin_id}", response_model=PluginState)
async def update_plugin(
    plugin_id: str,
    update: PluginStatusUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> PluginState:
    try:
        manifest = _registry.registration(plugin_id).manifest
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"}) from exc
    async with session_scope() as session:
        repo = PluginInstallationRepository(session, _registry)
        try:
            row = await repo.require(principal_id=identity.principal_id, plugin_id=plugin_id)
        except PluginNotInstalled as exc:
            raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_INSTALLED"}) from exc
        # Linearize enable/disable with connection rotation and uninstall.
        await session.scalar(
            select(PluginInstallation.id)
            .where(
                PluginInstallation.id == row.id,
                PluginInstallation.principal_id == identity.principal_id,
            )
            .with_for_update()
        )
        if update.enabled and row.installed_version != manifest.version:
            raise HTTPException(status_code=409, detail={"code": "PLUGIN_VERSION_MISMATCH"})
        if update.enabled and manifest.requires_connection:
            # Activation permits guarded reads, never a publish operation.
            if plugin_id != "wordpress":
                raise HTTPException(
                    status_code=409, detail={"code": "PLUGIN_CONNECTION_REQUIRED"}
                )
            connection = await session.scalar(
                select(PluginConnection).where(
                    PluginConnection.installation_id == row.id,
                    PluginConnection.principal_id == identity.principal_id,
                    PluginConnection.plugin_id == plugin_id,
                    PluginConnection.mode_id == "rest",
                    PluginConnection.schema_version == manifest.connection_schema_version,
                )
            )
            if connection is None:
                raise HTTPException(
                    status_code=409, detail={"code": "PLUGIN_CONNECTION_REQUIRED"}
                )
            secret_id = await session.scalar(
                select(EncryptedSecret.id).where(
                    EncryptedSecret.principal_id == identity.principal_id,
                    EncryptedSecret.owner_kind == "plugin_connection",
                    EncryptedSecret.owner_id == connection.id,
                    EncryptedSecret.slot == "application_password",
                )
            )
            if secret_id is None:
                raise HTTPException(
                    status_code=409, detail={"code": "PLUGIN_CREDENTIAL_REQUIRED"}
                )
        row = await repo.set_enabled(
            principal_id=identity.principal_id, plugin_id=plugin_id, enabled=update.enabled
        )
        return _plugin_state(manifest, row.installed_version, row.status == "enabled")


@router.delete("/{plugin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall_plugin(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> None:
    async with session_scope() as session:
        repo = PluginInstallationRepository(session, _registry)
        try:
            await repo.uninstall(principal_id=identity.principal_id, plugin_id=plugin_id)
        except (PluginNotInstalled, LookupError) as exc:
            raise HTTPException(
                status_code=404, detail={"code": "PLUGIN_NOT_INSTALLED"}
            ) from exc
