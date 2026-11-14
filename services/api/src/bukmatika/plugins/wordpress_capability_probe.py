"""Fail-closed WordPress capability observation.

These are user capabilities returned from the authenticated REST edit context,
not proof that an actual publish/media/taxonomy mutation is authorized.
"""

import base64
import json
from dataclasses import dataclass

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.pinned_https import get_wordpress_rest

_CAPABILITIES = (
    "edit_posts",
    "publish_posts",
    "upload_files",
    "assign_categories",
    "manage_categories",
    "edit_categories",
    "assign_post_tags",
    "manage_post_tags",
)


@dataclass(frozen=True, slots=True)
class WordPressCapabilityObservation:
    state: ConnectionHealth
    evidence_code: str
    capabilities: dict[str, bool]

    @property
    def capabilities_observed(self) -> bool:
        return self.state is ConnectionHealth.VERIFIED and (
            self.evidence_code == "AUTH_CAPABILITIES_200"
        )


def probe_wordpress_capabilities(
    site_url: str, username: str, application_password: str
) -> WordPressCapabilityObservation:
    """Inspect only reported capabilities at /users/me?context=edit."""
    denied = {key: False for key in _CAPABILITIES}

    def unavailable(code: str) -> WordPressCapabilityObservation:
        return WordPressCapabilityObservation(ConnectionHealth.UNAVAILABLE, code, denied)

    if not username or not application_password or any(
        ch in username for ch in (":", "\r", "\n")
    ):
        return unavailable("AUTH_CAPABILITY_INPUT_INVALID")

    token = base64.b64encode(f"{username}:{application_password}".encode()).decode("ascii")
    try:
        response = get_wordpress_rest(
            site_url,
            "/wp-json/wp/v2/users/me?context=edit",
            max_bytes=65536,
            authorization=f"Basic {token}",
        )
    except (OSError, ValueError):
        return unavailable("AUTH_CAPABILITY_TRANSPORT_UNAVAILABLE")
    if response.status_code in (401, 403):
        return unavailable("AUTH_CAPABILITY_REJECTED")
    if response.status_code != 200:
        return unavailable("AUTH_CAPABILITY_HTTP_UNEXPECTED")
    try:
        payload = json.loads(response.body)
    except (ValueError, UnicodeError):
        return unavailable("AUTH_CAPABILITY_RESPONSE_INVALID")
    if (
        not isinstance(payload, dict)
        or type(payload.get("id")) is not int
        or payload["id"] <= 0
        or not isinstance(payload.get("capabilities"), dict)
    ):
        return unavailable("AUTH_CAPABILITY_SCHEMA_INVALID")
    values = payload["capabilities"]
    if any(
        not isinstance(key, str) or type(value) is not bool
        for key, value in values.items()
    ):
        return unavailable("AUTH_CAPABILITY_SCHEMA_INVALID")
    return WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        {key: values.get(key, False) for key in _CAPABILITIES},
    )
