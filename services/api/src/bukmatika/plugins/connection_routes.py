"""Authenticated source-owned connection configuration, with redacted responses."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from bukmatika.config import get_settings
from bukmatika.credentials import InstallationCredentialKey
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence import session_scope
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth, configured_health
from bukmatika.plugins.connections import PluginConnectionInvalid, PluginConnectionRepository
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])
_registry = build_builtin_plugin_registry()


class ConnectionConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode_id: str
    values: dict[str, str | int]


class ConnectionState(BaseModel):
    plugin_id: str
    mode_id: str
    schema_version: int
    settings: dict[str, str | int]
    configured: bool


@router.put("/{plugin_id}/connection", response_model=ConnectionState)
async def configure_connection(
    plugin_id: str,
    request: ConnectionConfiguration,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> ConnectionState:
    # MySQL is metadata-only until the network and privilege security gates are proven.
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    if request.mode_id != "rest":
        raise HTTPException(status_code=409, detail={"code": "CONNECTION_MODE_UNAVAILABLE"})
    async with session_scope() as session:
        repository = PluginConnectionRepository(
            session,
            _registry,
            installation_key=InstallationCredentialKey(get_settings().credential_key_path),
        )
        try:
            row = await repository.configure(
                principal_id=identity.principal_id,
                plugin_id=plugin_id,
                mode_id=request.mode_id,
                values=request.values,
            )
        except PluginNotInstalled as exc:
            raise HTTPException(
                status_code=404, detail={"code": "PLUGIN_NOT_INSTALLED"}
            ) from exc
        except PluginConnectionInvalid as exc:
            raise HTTPException(
                status_code=422, detail={"code": "CONNECTION_SETTINGS_INVALID"}
            ) from exc
        return ConnectionState(
            plugin_id=row.plugin_id,
            mode_id=row.mode_id,
            schema_version=row.schema_version,
            settings=row.settings,
            configured=True,
        )


@router.get("/{plugin_id}/connection", response_model=ConnectionState)
async def read_connection(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> ConnectionState:
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        try:
            installation = await PluginInstallationRepository(session, _registry).require(
                principal_id=identity.principal_id, plugin_id=plugin_id
            )
        except PluginNotInstalled as exc:
            raise HTTPException(
                status_code=404, detail={"code": "PLUGIN_NOT_INSTALLED"}
            ) from exc
        row = await session.scalar(
            select(PluginConnection).where(
                PluginConnection.principal_id == identity.principal_id,
                PluginConnection.installation_id == installation.id,
                PluginConnection.plugin_id == plugin_id,
            )
        )
        if row is None:
            raise HTTPException(status_code=404, detail={"code": "CONNECTION_NOT_CONFIGURED"})
        return ConnectionState(
            plugin_id=row.plugin_id,
            mode_id=row.mode_id,
            schema_version=row.schema_version,
            settings=row.settings,
            configured=True,
        )


class ConnectionReadiness(BaseModel):
    plugin_id: str
    state: ConnectionHealth
    checked_at: str | None
    evidence: str | None


@router.get("/{plugin_id}/connection/health", response_model=ConnectionReadiness)
async def read_connection_health(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> ConnectionReadiness:
    """Read-only readiness. Never claim network verification from saved settings."""
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        try:
            installation = await PluginInstallationRepository(session, _registry).require(
                principal_id=identity.principal_id, plugin_id=plugin_id
            )
        except PluginNotInstalled as exc:
            raise HTTPException(
                status_code=404, detail={"code": "PLUGIN_NOT_INSTALLED"}
            ) from exc
        connection_id = await session.scalar(
            select(PluginConnection.id).where(
                PluginConnection.principal_id == identity.principal_id,
                PluginConnection.installation_id == installation.id,
                PluginConnection.plugin_id == plugin_id,
            )
        )
        receipt = configured_health(plugin_id, configured=connection_id is not None)
        return ConnectionReadiness(
            plugin_id=receipt.plugin_id,
            state=receipt.state,
            checked_at=receipt.checked_at,
            evidence=receipt.evidence,
        )
