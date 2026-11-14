from unittest.mock import patch

import pytest

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.pinned_https import WordPressHttpResponse, get_wordpress_rest
from bukmatika.plugins.wordpress_identity_probe import probe_wordpress_identity


def test_wordpress_identity_requires_valid_me_response_without_exposing_secrets() -> None:
    with patch(
        "bukmatika.plugins.wordpress_identity_probe.get_wordpress_rest",
        return_value=WordPressHttpResponse(200, b'{"id":42,"name":"Editor"}'),
    ) as get:
        receipt = probe_wordpress_identity(
            "https://example.com", "editor", "private-app-password"
        )
    assert receipt.state is ConnectionHealth.VERIFIED
    assert receipt.credentials_verified
    assert receipt.evidence_code == "AUTH_IDENTITY_200"
    assert "private-app-password" not in repr(receipt)
    assert get.call_args.kwargs["authorization"] == "Basic ZWRpdG9yOnByaXZhdGUtYXBwLXBhc3N3b3Jk"
    assert get.call_args.args == ("https://example.com", "/wp-json/wp/v2/users/me")


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (WordPressHttpResponse(401, b"{}"), "AUTH_REJECTED"),
        (WordPressHttpResponse(403, b"{}"), "AUTH_REJECTED"),
        (WordPressHttpResponse(200, b'{"id":0}'), "AUTH_IDENTITY_INVALID"),
        (WordPressHttpResponse(200, b'{"id":"42"}'), "AUTH_IDENTITY_INVALID"),
        (WordPressHttpResponse(200, b"garbage"), "AUTH_RESPONSE_INVALID"),
        (WordPressHttpResponse(302, b"{}"), "AUTH_HTTP_UNEXPECTED"),
    ],
)
def test_wordpress_auth_failures_never_claim_identity(
    response: WordPressHttpResponse, expected: str
) -> None:
    with patch(
        "bukmatika.plugins.wordpress_identity_probe.get_wordpress_rest",
        return_value=response,
    ):
        receipt = probe_wordpress_identity("https://example.com", "editor", "private")
    assert not receipt.credentials_verified
    assert receipt.state is ConnectionHealth.UNAVAILABLE
    assert receipt.evidence_code == expected


def test_transport_failure_does_not_expose_error_or_credentials() -> None:
    with patch(
        "bukmatika.plugins.wordpress_identity_probe.get_wordpress_rest",
        side_effect=OSError("private application password appeared in exception"),
    ):
        receipt = probe_wordpress_identity("https://example.com", "editor", "private")
    assert receipt.evidence_code == "AUTH_TRANSPORT_UNAVAILABLE"
    assert "private" not in repr(receipt)


def test_invalid_user_name_does_not_make_network_call() -> None:
    with patch("bukmatika.plugins.wordpress_identity_probe.get_wordpress_rest") as get:
        receipt = probe_wordpress_identity("https://example.com", "bad:user", "private")
    assert receipt.evidence_code == "AUTH_INPUT_INVALID"
    get.assert_not_called()


@pytest.mark.parametrize(
    "route", ["/wp-json/wp/v2/posts", "/wp-json/wp/v2/users", "/wp-json/wp/v2/types"]
)
def test_auth_header_cannot_be_sent_to_other_routes(route: str) -> None:
    with pytest.raises(ValueError):
        get_wordpress_rest(
            "https://example.com", route, authorization="Basic dXNlcjpwYXNz"
        )
