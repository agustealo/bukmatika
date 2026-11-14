from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from bukmatika.credentials import CredentialUnavailable
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.public_probe_routes import (
    PublicRestProbeStatus,
    WordPressCapabilityStatus,
    WordPressIdentityStatus,
    _cooldown_active,
    check_wordpress_capabilities,
    check_wordpress_identity,
    get_public_rest_probe,
    get_wordpress_capability_status,
    get_wordpress_identity_status,
    router,
    run_public_rest_probe,
)
from bukmatika.plugins.wordpress_capability_probe import (
    WORDPRESS_CAPABILITY_KEYS,
    WordPressCapabilityObservation,
)


@asynccontextmanager
async def _fake_session():
    yield object()


def test_probe_routes_require_auth_and_response_explicitly_disclaims_credentials() -> None:
    methods = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    path = "/v1/plugins/{plugin_id}/connection/public-probe"
    assert (path, ("GET",)) in methods
    assert (path, ("POST",)) in methods
    assert all(route.dependant.dependencies for route in router.routes)
    assert PublicRestProbeStatus.model_fields["credentials_verified"].default is False
    assert PublicRestProbeStatus.model_fields["publishing_authorized"].default is False


def test_cooldown_is_based_on_actual_timestamp() -> None:
    now = datetime.now(UTC)
    assert _cooldown_active(now, now)
    assert not _cooldown_active(now.replace(year=2020), now)


@pytest.mark.asyncio
async def test_read_public_probe_does_not_trigger_network() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    receipt = SimpleNamespace(
        state="verified",
        evidence_code="PUBLIC_REST_TYPES_200",
        checked_at=datetime.now(UTC),
    )
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ) as lookup,
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.record_public_wordpress_probe",
            new_callable=AsyncMock,
        ) as network,
    ):
        repository.return_value.latest = AsyncMock(return_value=receipt)
        result = await get_public_rest_probe("wordpress", identity)
    assert result.state is ConnectionHealth.VERIFIED
    assert result.scope == "anonymous_public_rest"
    assert not result.credentials_verified
    assert not result.publishing_authorized
    assert lookup.await_args.kwargs["lock"] is False
    assert repository.return_value.latest.await_args.kwargs["evidence_codes"]
    network.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_probe_records_observation_and_disclaims_authentication() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    receipt = SimpleNamespace(
        state="verified",
        evidence_code="PUBLIC_REST_TYPES_200",
        checked_at=datetime.now(UTC),
    )
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ) as lookup,
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.record_public_wordpress_probe",
            new_callable=AsyncMock,
        ) as network,
    ):
        repository.return_value.latest = AsyncMock(side_effect=[None, receipt])
        result = await run_public_rest_probe("wordpress", identity)
    network.assert_awaited_once()
    assert lookup.await_args.kwargs["lock"] is True
    assert result.state is ConnectionHealth.VERIFIED
    assert result.evidence_code == "PUBLIC_REST_TYPES_200"
    assert not result.credentials_verified
    assert not result.publishing_authorized


@pytest.mark.asyncio
async def test_repeated_public_probe_is_throttled_without_network() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    recent = SimpleNamespace(checked_at=datetime.now(UTC))
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.record_public_wordpress_probe",
            new_callable=AsyncMock,
        ) as network,
        pytest.raises(HTTPException) as exc,
    ):
        repository.return_value.latest = AsyncMock(return_value=recent)
        await run_public_rest_probe("wordpress", identity)
    assert exc.value.status_code == 429
    network.assert_not_awaited()


def test_identity_routes_are_authenticated_and_not_publishing_authorized() -> None:
    path = "/v1/plugins/{plugin_id}/connection/identity-probe"
    methods = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    assert (path, ("GET",)) in methods
    assert (path, ("POST",)) in methods
    assert WordPressIdentityStatus.model_fields["publishing_authorized"].default is False


@pytest.mark.asyncio
async def test_identity_status_reads_scoped_evidence_without_network() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    receipt = SimpleNamespace(
        state="verified",
        evidence_code="AUTH_IDENTITY_200",
        checked_at=datetime.now(UTC),
    )
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.verify_wordpress_identity",
            new_callable=AsyncMock,
        ) as network,
    ):
        repository.return_value.latest = AsyncMock(return_value=receipt)
        result = await get_wordpress_identity_status("wordpress", identity)
    assert result.scope == "application_password_identity"
    assert result.credentials_verified
    assert not result.publishing_authorized
    assert repository.return_value.latest.await_args.kwargs["evidence_codes"]
    network.assert_not_awaited()


@pytest.mark.asyncio
async def test_identity_post_uses_vault_and_returns_separate_credential_scope() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    receipt = SimpleNamespace(
        state="verified",
        evidence_code="AUTH_IDENTITY_200",
        checked_at=datetime.now(UTC),
    )
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ) as lookup,
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.verify_wordpress_identity",
            new_callable=AsyncMock,
        ) as verification,
        patch(
            "bukmatika.plugins.public_probe_routes.get_settings"
        ) as settings,
    ):
        repository.return_value.latest = AsyncMock(side_effect=[None, receipt])
        settings.return_value.credential_key_path = "/tmp/bukmatika-test-key"
        result = await check_wordpress_identity("wordpress", identity)
    verification.assert_awaited_once()
    assert verification.await_args.kwargs["principal_id"] == identity.principal_id
    assert lookup.await_args.kwargs["lock"] is True
    assert result.credentials_verified
    assert not result.publishing_authorized
    assert result.evidence_code == "AUTH_IDENTITY_200"


@pytest.mark.asyncio
async def test_missing_identity_credential_never_claims_success() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.verify_wordpress_identity",
            new_callable=AsyncMock,
            side_effect=CredentialUnavailable("private credential"),
        ),
        patch("bukmatika.plugins.public_probe_routes.get_settings") as settings,
        pytest.raises(HTTPException) as exc,
    ):
        repository.return_value.latest = AsyncMock(return_value=None)
        settings.return_value.credential_key_path = "/tmp/bukmatika-test-key"
        await check_wordpress_identity("wordpress", identity)
    assert exc.value.status_code == 409
    assert exc.value.detail == {"code": "CREDENTIAL_UNAVAILABLE"}


def _capability_values(*, edit_posts: bool = False, publish_posts: bool = False) -> dict[str, bool]:
    values = {key: False for key in WORDPRESS_CAPABILITY_KEYS}
    values["edit_posts"] = edit_posts
    values["publish_posts"] = publish_posts
    return values


def _capability_snapshot(
    *,
    state: str = "verified",
    evidence_code: str = "AUTH_CAPABILITIES_200",
    edit_posts: bool = False,
    publish_posts: bool = False,
    checked_at: datetime | None = None,
) -> SimpleNamespace:
    values = _capability_values(edit_posts=edit_posts, publish_posts=publish_posts)
    return SimpleNamespace(
        state=state,
        evidence_code=evidence_code,
        checked_at=checked_at or datetime.now(UTC),
        **values,
    )


def test_capability_routes_are_authenticated_and_not_publishing_authority() -> None:
    path = "/v1/plugins/{plugin_id}/connection/capability-probe"
    methods = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    assert (path, ("GET",)) in methods
    assert (path, ("POST",)) in methods
    assert WordPressCapabilityStatus.model_fields["publishing_authorized"].default is False


@pytest.mark.asyncio
async def test_capability_status_restores_scoped_snapshot_without_network() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    snapshot = _capability_snapshot(edit_posts=True, publish_posts=False)
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ) as lookup,
        patch(
            "bukmatika.plugins.public_probe_routes.WordPressCapabilitySnapshotRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.verify_wordpress_capabilities",
            new_callable=AsyncMock,
        ) as network,
    ):
        repository.return_value.latest = AsyncMock(return_value=snapshot)
        result = await get_wordpress_capability_status("wordpress", identity)
    assert lookup.await_args.kwargs["lock"] is False
    assert result.state is ConnectionHealth.VERIFIED
    assert result.capabilities_observed
    assert result.capabilities["edit_posts"]
    assert not result.capabilities["publish_posts"]
    assert result.scope == "observed_wordpress_capabilities"
    assert not result.publishing_authorized
    network.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_status_without_snapshot_is_not_tested_and_denies_all_flags() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        patch(
            "bukmatika.plugins.public_probe_routes.WordPressCapabilitySnapshotRepository"
        ) as repository,
    ):
        repository.return_value.latest = AsyncMock(return_value=None)
        result = await get_wordpress_capability_status("wordpress", identity)
    assert result.state is ConnectionHealth.NOT_TESTED
    assert result.checked_at is None
    assert result.evidence_code is None
    assert not result.capabilities_observed
    assert set(result.capabilities) == set(WORDPRESS_CAPABILITY_KEYS)
    assert not any(result.capabilities.values())
    assert not result.publishing_authorized


@pytest.mark.asyncio
async def test_capability_probe_returns_persisted_flags_without_authorizing_writes() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    checked_at = datetime.now(UTC)
    receipt = SimpleNamespace(
        state="verified",
        evidence_code="AUTH_CAPABILITIES_200",
        checked_at=checked_at,
    )
    snapshot = _capability_snapshot(
        edit_posts=True,
        publish_posts=False,
        checked_at=checked_at,
    )
    observation = WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        _capability_values(edit_posts=True, publish_posts=False),
    )
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ) as lookup,
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as receipts,
        patch(
            "bukmatika.plugins.public_probe_routes.WordPressCapabilitySnapshotRepository"
        ) as snapshots,
        patch(
            "bukmatika.plugins.public_probe_routes.verify_wordpress_capabilities",
            new_callable=AsyncMock,
            return_value=observation,
        ) as verify,
        patch("bukmatika.plugins.public_probe_routes.get_settings") as settings,
    ):
        receipts.return_value.latest = AsyncMock(side_effect=[None, receipt])
        snapshots.return_value.latest = AsyncMock(return_value=snapshot)
        settings.return_value.credential_key_path = "/tmp/bukmatika-test-key"
        result = await check_wordpress_capabilities("wordpress", identity)
    verify.assert_awaited_once()
    assert lookup.await_args.kwargs["lock"] is True
    assert result.capabilities_observed
    assert result.capabilities["edit_posts"]
    assert not result.capabilities["publish_posts"]
    assert result.checked_at == checked_at
    assert result.scope == "observed_wordpress_capabilities"
    assert not result.publishing_authorized


@pytest.mark.asyncio
async def test_capability_probe_cooldown_never_sends_credentials_or_network() -> None:
    identity = SimpleNamespace(principal_id=uuid4())
    connection = SimpleNamespace(id=uuid4())
    with (
        patch("bukmatika.plugins.public_probe_routes.session_scope", _fake_session),
        patch(
            "bukmatika.plugins.public_probe_routes._connection",
            new_callable=AsyncMock,
            return_value=connection,
        ),
        patch(
            "bukmatika.plugins.public_probe_routes.PluginHealthReceiptRepository"
        ) as repository,
        patch(
            "bukmatika.plugins.public_probe_routes.verify_wordpress_capabilities",
            new_callable=AsyncMock,
        ) as verify,
        pytest.raises(HTTPException) as exc,
    ):
        repository.return_value.latest = AsyncMock(
            return_value=SimpleNamespace(checked_at=datetime.now(UTC))
        )
        await check_wordpress_capabilities("wordpress", identity)
    assert exc.value.status_code == 429
    verify.assert_not_awaited()
