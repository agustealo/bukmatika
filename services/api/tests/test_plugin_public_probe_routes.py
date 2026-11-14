from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.public_probe_routes import (
    PublicRestProbeStatus,
    _cooldown_active,
    get_public_rest_probe,
    router,
    run_public_rest_probe,
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
