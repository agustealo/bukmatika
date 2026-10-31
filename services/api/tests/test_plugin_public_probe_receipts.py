from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.public_probe_receipts import record_public_wordpress_probe
from bukmatika.plugins.wordpress_probe import WordPressProbeObservation


@pytest.mark.asyncio
async def test_public_probe_writes_only_actual_observation() -> None:
    principal_id = uuid4()
    connection_id = uuid4()
    session = AsyncMock()
    connection = type(
        "Connection",
        (),
        {"id": connection_id, "settings": {"site_url": "https://example.com"}},
    )()
    session.scalar.return_value = connection
    observation = WordPressProbeObservation(
        ConnectionHealth.VERIFIED, "PUBLIC_REST_TYPES_200"
    )
    with (
        patch(
            "bukmatika.plugins.public_probe_receipts.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=observation,
        ) as probe,
        patch(
            "bukmatika.plugins.public_probe_receipts.PluginHealthReceiptRepository"
        ) as repository,
    ):
        result = await record_public_wordpress_probe(
            session, principal_id=principal_id, connection_id=connection_id
        )
    assert result is observation
    assert probe.await_count == 1
    assert probe.await_args.args[1] == "https://example.com"
    assert (
        repository.return_value.record.await_args.kwargs["principal_id"] == principal_id
    )
    assert (
        repository.return_value.record.await_args.kwargs["connection_id"] == connection_id
    )
    assert repository.return_value.record.await_args.kwargs["evidence_code"] == (
        "PUBLIC_REST_TYPES_200"
    )


@pytest.mark.asyncio
async def test_probe_rejects_unowned_connection_before_network_access() -> None:
    session = AsyncMock()
    session.scalar.return_value = None
    with patch(
        "bukmatika.plugins.public_probe_receipts.asyncio.to_thread",
        new_callable=AsyncMock,
    ) as probe:
        with pytest.raises(LookupError):
            await record_public_wordpress_probe(
                session, principal_id=uuid4(), connection_id=uuid4()
            )
    probe.assert_not_awaited()
