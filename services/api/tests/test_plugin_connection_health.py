import pytest

from bukmatika.plugins.connection_health import (
    ConnectionHealth,
    ConnectionHealthReceipt,
    configured_health,
)


def test_configured_connection_is_not_claimed_healthy() -> None:
    receipt = configured_health("wordpress", configured=True)
    assert receipt.state is ConnectionHealth.NOT_TESTED
    assert receipt.checked_at is None
    assert receipt.evidence is None


def test_missing_configuration_is_not_connected() -> None:
    assert (
        configured_health("wordpress", configured=False).state
        is ConnectionHealth.NOT_CONFIGURED
    )


def test_verified_health_requires_real_evidence() -> None:
    with pytest.raises(ValueError):
        ConnectionHealthReceipt(plugin_id="wordpress", state=ConnectionHealth.VERIFIED)


def test_untested_health_cannot_include_fake_observation() -> None:
    with pytest.raises(ValueError):
        ConnectionHealthReceipt(
            plugin_id="wordpress",
            state=ConnectionHealth.NOT_TESTED,
            checked_at="2026-10-20T00:00:00Z",
        )
