"""Credential identity probe, deliberately separate from publishing authorization.

This probe confirms that a WordPress Application Password authenticates at
/users/me. It cannot establish edit_posts, upload_files, or any write capability.
"""

import base64
import json
from dataclasses import dataclass

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.pinned_https import get_wordpress_rest


@dataclass(frozen=True, slots=True)
class WordPressIdentityObservation:
    state: ConnectionHealth
    evidence_code: str

    @property
    def credentials_verified(self) -> bool:
        return (
            self.state is ConnectionHealth.VERIFIED
            and self.evidence_code == "AUTH_IDENTITY_200"
        )


def probe_wordpress_identity(
    site_url: str,
    username: str,
    application_password: str,
) -> WordPressIdentityObservation:
    """Verify identity with pinned HTTPS, returning only non-sensitive evidence."""
    if not username or not application_password or any(
        char in username for char in ("\r", "\n", ":")
    ):
        return WordPressIdentityObservation(
            ConnectionHealth.UNAVAILABLE, "AUTH_INPUT_INVALID"
        )
    token = base64.b64encode(
        f"{username}:{application_password}".encode()
    ).decode("ascii")
    try:
        response = get_wordpress_rest(
            site_url,
            "/wp-json/wp/v2/users/me",
            max_bytes=65536,
            authorization=f"Basic {token}",
        )
    except (OSError, ValueError):
        return WordPressIdentityObservation(
            ConnectionHealth.UNAVAILABLE, "AUTH_TRANSPORT_UNAVAILABLE"
        )
    if response.status_code in (401, 403):
        return WordPressIdentityObservation(
            ConnectionHealth.UNAVAILABLE, "AUTH_REJECTED"
        )
    if response.status_code != 200:
        return WordPressIdentityObservation(
            ConnectionHealth.UNAVAILABLE, "AUTH_HTTP_UNEXPECTED"
        )
    try:
        payload = json.loads(response.body)
    except (UnicodeError, ValueError):
        return WordPressIdentityObservation(
            ConnectionHealth.UNAVAILABLE, "AUTH_RESPONSE_INVALID"
        )
    if (
        not isinstance(payload, dict)
        or type(payload.get("id")) is not int
        or payload["id"] <= 0
    ):
        return WordPressIdentityObservation(
            ConnectionHealth.UNAVAILABLE, "AUTH_IDENTITY_INVALID"
        )
    return WordPressIdentityObservation(
        ConnectionHealth.VERIFIED, "AUTH_IDENTITY_200"
    )
