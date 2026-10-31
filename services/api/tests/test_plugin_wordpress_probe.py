from unittest.mock import patch

import pytest

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.pinned_https import WordPressHttpResponse
from bukmatika.plugins.wordpress_probe import probe_public_wordpress_rest


def test_real_schema_response_produces_scoped_public_endpoint_observation() -> None:
    with patch(
        "bukmatika.plugins.wordpress_probe.get_wordpress_rest",
        return_value=WordPressHttpResponse(200, b'{"post":{"slug":"post"}}'),
    ) as request:
        result = probe_public_wordpress_rest("https://example.com")
    request.assert_called_once_with(
        "https://example.com", "/wp-json/wp/v2/types", max_bytes=65536
    )
    assert result.state is ConnectionHealth.VERIFIED
    assert result.evidence_code == "PUBLIC_REST_TYPES_200"


@pytest.mark.parametrize(
    "response",
    [
        WordPressHttpResponse(401, b"{}"),
        WordPressHttpResponse(200, b"not json"),
        WordPressHttpResponse(200, b'{"pages":{}}'),
    ],
)
def test_unverified_public_endpoint_never_claims_success(
    response: WordPressHttpResponse,
) -> None:
    with patch("bukmatika.plugins.wordpress_probe.get_wordpress_rest", return_value=response):
        result = probe_public_wordpress_rest("https://example.com")
    assert result.state is ConnectionHealth.UNAVAILABLE


def test_transport_failure_is_not_claimed_verified() -> None:
    with patch(
        "bukmatika.plugins.wordpress_probe.get_wordpress_rest",
        side_effect=OSError("connection failed"),
    ):
        result = probe_public_wordpress_rest("https://example.com")
    assert result.state is ConnectionHealth.UNAVAILABLE
    assert result.evidence_code == "TRANSPORT_UNAVAILABLE"
