"""Read-only, evidence-based WordPress REST probe.

This module performs no authentication and does not authorize publishing.
A reachable public endpoint proves transport availability, not credential validity.
"""

import json
from dataclasses import dataclass

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.pinned_https import get_wordpress_rest


@dataclass(frozen=True, slots=True)
class WordPressProbeObservation:
    state: ConnectionHealth
    evidence_code: str


def probe_public_wordpress_rest(site_url: str) -> WordPressProbeObservation:
    """Check a bounded, pinned-IP public REST endpoint without sending credentials."""
    try:
        response = get_wordpress_rest(site_url, "/wp-json/wp/v2/types", max_bytes=65536)
    except (OSError, ValueError):
        return WordPressProbeObservation(ConnectionHealth.UNAVAILABLE, "TRANSPORT_UNAVAILABLE")
    if response.status_code != 200:
        return WordPressProbeObservation(ConnectionHealth.UNAVAILABLE, "HTTP_NOT_SUCCESSFUL")
    try:
        payload = json.loads(response.body)
    except (UnicodeError, ValueError):
        return WordPressProbeObservation(ConnectionHealth.UNAVAILABLE, "INVALID_JSON")
    if not isinstance(payload, dict) or "post" not in payload:
        return WordPressProbeObservation(ConnectionHealth.UNAVAILABLE, "INVALID_WORDPRESS_SCHEMA")
    return WordPressProbeObservation(ConnectionHealth.VERIFIED, "PUBLIC_REST_TYPES_200")
