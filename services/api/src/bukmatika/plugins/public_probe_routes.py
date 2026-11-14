"""Authenticated, rate-limited public WordPress REST connectivity observations.

These checks never transmit application passwords. Public REST reachability does
not validate a WordPress account or grant any publishing capability.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence import session_scope
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled
from bukmatika.plugins.public_probe_receipts import record_public_wordpress_probe

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])
_registry = build_builtin_plugin_registry()
_PROBE_COOLDOWN = timedelta(seconds=60)


class PublicRestProbeStatus(BaseModel):
    plugin_id: str
    scope: str = "anonymous_public_rest"
    state: ConnectionHealth
    checked_at: datetime | None
    evidence_code: str | None
    credentials_verified: bool = False
    publishing_authorized: bool = False


def _format_public_rest_status(
    state: ConnectionHealth,
    *,
    checked_at: datetime | None = None,
    evidence_code: str | None = None,
) -> PublicRestProbeStatus:
    return PublicRestProbeStatus(
        plugin_id="wordpress",
        state=state,
        checked_at=checked_at,
        evidence_code=evidence_code,
    )


def _cooldown_active(checked_at: datetime, now: datetime) -> bool:
    return checked_at.tzinfo is not None and now - checked_at < _PROBE_COOLDOWN


async def _connection(
    session: AsyncSession,
    *,
    principal_id: UUID,
    plugin_id: str,
    lock: bool,
) -> PluginConnection | None:
    try:
        installation = await PluginInstallationRepository(session, _registry).require(
            principal_id=principal_id, plugin_id=plugin_id
        )
    except PluginNotInstalled as exc:
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_INSTALLED"}) from exc
    query = select(PluginConnection).where(
        PluginConnection.principal_id == principal_id,
        PluginConnection.installation_id == installation.id,
        PluginConnection.plugin_id == plugin_id,
        PluginConnection.mode_id == "rest",
    )
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


@router.get("/{plugin_id}/connection/public-probe", response_model=PublicRestProbeStatus)
async def get_public_rest_probe(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> PublicRestProbeStatus:
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        connection = await _connection(
            session, principal_id=identity.principal_id, plugin_id=plugin_id, lock=False
        )
        if connection is None:
            return _format_public_rest_status(ConnectionHealth.NOT_CONFIGURED)
        receipt = await PluginHealthReceiptRepository(session).latest(
            principal_id=identity.principal_id, connection_id=connection.id
        )
        if receipt is None:
            return _format_public_rest_status(ConnectionHealth.NOT_TESTED)
        return _format_public_rest_status(
            ConnectionHealth(receipt.state),
            checked_at=receipt.checked_at,
            evidence_code=receipt.evidence_code,
        )


@router.post("/{plugin_id}/connection/public-probe", response_model=PublicRestProbeStatus)
async def run_public_rest_probe(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> PublicRestProbeStatus:
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        connection = await _connection(
            session, principal_id=identity.principal_id, plugin_id=plugin_id, lock=True
        )
        if connection is None:
            raise HTTPException(status_code=409, detail={"code": "CONNECTION_NOT_CONFIGURED"})
        receipts = PluginHealthReceiptRepository(session)
        previous = await receipts.latest(
            principal_id=identity.principal_id, connection_id=connection.id
        )
        if previous is not None and _cooldown_active(previous.checked_at, datetime.now(UTC)):
            raise HTTPException(status_code=429, detail={"code": "PROBE_COOLDOWN"})
        await record_public_wordpress_probe(
            session, principal_id=identity.principal_id, connection_id=connection.id
        )
        recorded = await receipts.latest(
            principal_id=identity.principal_id, connection_id=connection.id
        )
        if recorded is None:
            raise RuntimeError("Public WordPress probe did not persist an observation")
        return _format_public_rest_status(
            ConnectionHealth(recorded.state),
            checked_at=recorded.checked_at,
            evidence_code=recorded.evidence_code,
        )
