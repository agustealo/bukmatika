"""Honest, non-secret connection readiness representation.

Configured settings are not evidence that a remote WordPress site was reached.
"""

from dataclasses import dataclass
from enum import StrEnum


class ConnectionHealth(StrEnum):
    NOT_CONFIGURED = "not_configured"
    NOT_TESTED = "not_tested"
    VERIFIED = "verified"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ConnectionHealthReceipt:
    plugin_id: str
    state: ConnectionHealth
    checked_at: str | None = None
    evidence: str | None = None

    def __post_init__(self) -> None:
        if self.state is ConnectionHealth.VERIFIED and (
            not self.checked_at or not self.evidence
        ):
            raise ValueError("Verified health requires timestamped evidence")
        if self.state in {ConnectionHealth.NOT_TESTED, ConnectionHealth.NOT_CONFIGURED} and (
            self.checked_at is not None or self.evidence is not None
        ):
            raise ValueError("Untested configuration cannot claim observations")


def configured_health(plugin_id: str, *, configured: bool) -> ConnectionHealthReceipt:
    return ConnectionHealthReceipt(
        plugin_id=plugin_id,
        state=ConnectionHealth.NOT_TESTED if configured else ConnectionHealth.NOT_CONFIGURED,
    )
