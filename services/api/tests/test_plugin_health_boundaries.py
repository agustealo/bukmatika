"""Health states must never imply a live network probe without evidence."""

from unittest.mock import patch

import pytest

from bukmatika.plugins.connection_health import (
    ConnectionHealth,
    ConnectionHealthReceipt,
    configured_health,
)


@pytest.mark.parametrize("configured", [True, False])
def test_health_readiness_does_not_probe_network(configured: bool) -> None:
    with (
        patch("bukmatika.plugins.pinned_https.get_wordpress_rest") as request,
        patch("bukmatika.plugins.dns_policy.socket.getaddrinfo") as dns,
    ):
        receipt = configured_health("wordpress", configured=configured)
    request.assert_not_called()
    dns.assert_not_called()
    assert receipt.state in {
        ConnectionHealth.NOT_TESTED,
        ConnectionHealth.NOT_CONFIGURED,
    }
    assert receipt.evidence is None


@pytest.mark.parametrize(
    "state",
    [ConnectionHealth.NOT_TESTED, ConnectionHealth.NOT_CONFIGURED],
)
def test_unverified_states_reject_fake_evidence(state: ConnectionHealth) -> None:
    with pytest.raises(ValueError):
        ConnectionHealthReceipt(
            plugin_id="wordpress",
            state=state,
            checked_at="2026-10-20T00:00:00Z",
            evidence="response 200",
        )


def test_verified_state_requires_both_timestamp_and_evidence() -> None:
    with pytest.raises(ValueError):
        ConnectionHealthReceipt(
            plugin_id="wordpress",
            state=ConnectionHealth.VERIFIED,
            evidence="http-200",
        )
