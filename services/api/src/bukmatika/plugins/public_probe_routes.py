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

from bukmatika.config import get_settings
from bukmatika.credentials import (
    CredentialIntegrityError,
    CredentialKeyUnavailable,
    CredentialUnavailable,
    InstallationCredentialKey,
)
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence import session_scope
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.health_receipts import PluginHealthReceiptRepository
from bukmatika.plugins.installations import PluginInstallationRepository, PluginNotInstalled
from bukmatika.plugins.public_probe_receipts import record_public_wordpress_probe
from bukmatika.plugins.wordpress_capability_probe import (
    WORDPRESS_CAPABILITY_EVIDENCE_CODES,
    WORDPRESS_CAPABILITY_KEYS,
)
from bukmatika.plugins.wordpress_capability_service import verify_wordpress_capabilities
from bukmatika.plugins.wordpress_capability_snapshots import (
    WordPressCapabilitySnapshotRepository,
    snapshot_capabilities,
)
from bukmatika.plugins.wordpress_identity_service import verify_wordpress_identity

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])
_registry = build_builtin_plugin_registry()
_PROBE_COOLDOWN = timedelta(seconds=60)
_IDENTITY_EVIDENCE_CODES = (
    "AUTH_IDENTITY_200",
    "AUTH_INPUT_INVALID",
    "AUTH_TRANSPORT_UNAVAILABLE",
    "AUTH_REJECTED",
    "AUTH_HTTP_UNEXPECTED",
    "AUTH_RESPONSE_INVALID",
    "AUTH_IDENTITY_INVALID",
)
_PUBLIC_EVIDENCE_CODES = (
    "PUBLIC_REST_TYPES_200",
    "TRANSPORT_UNAVAILABLE",
    "HTTP_NOT_SUCCESSFUL",
    "INVALID_JSON",
    "INVALID_WORDPRESS_SCHEMA",
)


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
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=_PUBLIC_EVIDENCE_CODES,
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
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=_PUBLIC_EVIDENCE_CODES,
        )
        if previous is not None and _cooldown_active(previous.checked_at, datetime.now(UTC)):
            raise HTTPException(status_code=429, detail={"code": "PROBE_COOLDOWN"})
        await record_public_wordpress_probe(
            session,
            principal_id=identity.principal_id,
            connection_id=connection.id,
        )
        recorded = await receipts.latest(
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=_PUBLIC_EVIDENCE_CODES,
        )
        if recorded is None:
            raise RuntimeError("Public WordPress probe did not persist an observation")
        return _format_public_rest_status(
            ConnectionHealth(recorded.state),
            checked_at=recorded.checked_at,
            evidence_code=recorded.evidence_code,
        )


class WordPressIdentityStatus(BaseModel):
    plugin_id: str
    scope: str = "application_password_identity"
    state: ConnectionHealth
    checked_at: datetime | None
    evidence_code: str | None
    credentials_verified: bool
    publishing_authorized: bool = False


def _identity_status(
    state: ConnectionHealth,
    *,
    checked_at: datetime | None = None,
    evidence_code: str | None = None,
) -> WordPressIdentityStatus:
    return WordPressIdentityStatus(
        plugin_id="wordpress",
        state=state,
        checked_at=checked_at,
        evidence_code=evidence_code,
        credentials_verified=(
            state is ConnectionHealth.VERIFIED
            and evidence_code == "AUTH_IDENTITY_200"
        ),
    )


@router.get("/{plugin_id}/connection/identity-probe", response_model=WordPressIdentityStatus)
async def get_wordpress_identity_status(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> WordPressIdentityStatus:
    """Read credential evidence, without performing a network request."""
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        connection = await _connection(
            session, principal_id=identity.principal_id, plugin_id=plugin_id, lock=False
        )
        if connection is None:
            return _identity_status(ConnectionHealth.NOT_CONFIGURED)
        receipt = await PluginHealthReceiptRepository(session).latest(
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=_IDENTITY_EVIDENCE_CODES,
        )
        if receipt is None:
            return _identity_status(ConnectionHealth.NOT_TESTED)
        return _identity_status(
            ConnectionHealth(receipt.state),
            checked_at=receipt.checked_at,
            evidence_code=receipt.evidence_code,
        )


@router.post("/{plugin_id}/connection/identity-probe", response_model=WordPressIdentityStatus)
async def check_wordpress_identity(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> WordPressIdentityStatus:
    """Verify saved credentials only. Never assert publishing authorization."""
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
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=_IDENTITY_EVIDENCE_CODES,
        )
        if previous is not None and _cooldown_active(previous.checked_at, datetime.now(UTC)):
            raise HTTPException(status_code=429, detail={"code": "PROBE_COOLDOWN"})
        try:
            await verify_wordpress_identity(
                session,
                principal_id=identity.principal_id,
                connection_id=connection.id,
                installation_key=InstallationCredentialKey(
                    get_settings().credential_key_path
                ),
            )
        except CredentialUnavailable as exc:
            raise HTTPException(
                status_code=409, detail={"code": "CREDENTIAL_UNAVAILABLE"}
            ) from exc
        except (CredentialIntegrityError, CredentialKeyUnavailable) as exc:
            raise HTTPException(
                status_code=503, detail={"code": "CREDENTIAL_VERIFICATION_UNAVAILABLE"}
            ) from exc
        recorded = await receipts.latest(
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=_IDENTITY_EVIDENCE_CODES,
        )
        if recorded is None:
            raise RuntimeError("WordPress identity probe did not record an observation")
        return _identity_status(
            ConnectionHealth(recorded.state),
            checked_at=recorded.checked_at,
            evidence_code=recorded.evidence_code,
        )


class WordPressCapabilityStatus(BaseModel):
    plugin_id: str = "wordpress"
    scope: str = "observed_wordpress_capabilities"
    state: ConnectionHealth
    checked_at: datetime | None
    evidence_code: str | None
    capabilities_observed: bool
    capabilities: dict[str, bool]
    publishing_authorized: bool = False


def _capability_status(
    state: ConnectionHealth,
    *,
    checked_at: datetime | None = None,
    evidence_code: str | None = None,
    capabilities: dict[str, bool] | None = None,
) -> WordPressCapabilityStatus:
    values = (
        capabilities
        if capabilities is not None
        else {key: False for key in WORDPRESS_CAPABILITY_KEYS}
    )
    return WordPressCapabilityStatus(
        state=state,
        checked_at=checked_at,
        evidence_code=evidence_code,
        capabilities_observed=(
            state is ConnectionHealth.VERIFIED
            and evidence_code == "AUTH_CAPABILITIES_200"
        ),
        capabilities=values,
    )


@router.get(
    "/{plugin_id}/connection/capability-probe",
    response_model=WordPressCapabilityStatus,
)
async def get_wordpress_capability_status(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> WordPressCapabilityStatus:
    """Read the latest durable capability observation without network access."""
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        connection = await _connection(
            session, principal_id=identity.principal_id, plugin_id=plugin_id, lock=False
        )
        if connection is None:
            return _capability_status(ConnectionHealth.NOT_CONFIGURED)
        snapshot = await WordPressCapabilitySnapshotRepository(session).latest(
            principal_id=identity.principal_id,
            connection_id=connection.id,
        )
        if snapshot is None:
            return _capability_status(ConnectionHealth.NOT_TESTED)
        return _capability_status(
            ConnectionHealth(snapshot.state),
            checked_at=snapshot.checked_at,
            evidence_code=snapshot.evidence_code,
            capabilities=snapshot_capabilities(snapshot),
        )


@router.post(
    "/{plugin_id}/connection/capability-probe",
    response_model=WordPressCapabilityStatus,
)
async def check_wordpress_capabilities(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> WordPressCapabilityStatus:
    """Run one authenticated capability inspection, never a publishing operation."""
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
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=WORDPRESS_CAPABILITY_EVIDENCE_CODES,
        )
        if previous is not None and _cooldown_active(previous.checked_at, datetime.now(UTC)):
            raise HTTPException(status_code=429, detail={"code": "PROBE_COOLDOWN"})
        try:
            observation = await verify_wordpress_capabilities(
                session,
                principal_id=identity.principal_id,
                connection_id=connection.id,
                installation_key=InstallationCredentialKey(
                    get_settings().credential_key_path
                ),
            )
        except CredentialUnavailable as exc:
            raise HTTPException(
                status_code=409, detail={"code": "CREDENTIAL_UNAVAILABLE"}
            ) from exc
        except (CredentialIntegrityError, CredentialKeyUnavailable) as exc:
            raise HTTPException(
                status_code=503, detail={"code": "CREDENTIAL_VERIFICATION_UNAVAILABLE"}
            ) from exc
        recorded = await receipts.latest(
            principal_id=identity.principal_id,
            connection_id=connection.id,
            evidence_codes=WORDPRESS_CAPABILITY_EVIDENCE_CODES,
        )
        snapshot = await WordPressCapabilitySnapshotRepository(session).latest(
            principal_id=identity.principal_id,
            connection_id=connection.id,
        )
        if (
            recorded is None
            or snapshot is None
            or recorded.evidence_code != observation.evidence_code
            or snapshot.evidence_code != observation.evidence_code
            or snapshot.checked_at != recorded.checked_at
        ):
            raise RuntimeError("WordPress capability observation was not persisted consistently")
        return _capability_status(
            ConnectionHealth(snapshot.state),
            checked_at=snapshot.checked_at,
            evidence_code=snapshot.evidence_code,
            capabilities=snapshot_capabilities(snapshot),
        )
