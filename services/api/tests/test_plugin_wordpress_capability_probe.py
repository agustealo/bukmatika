import json
from unittest.mock import patch

import pytest

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.pinned_https import WordPressHttpResponse, get_wordpress_rest
from bukmatika.plugins.wordpress_capability_probe import probe_wordpress_capabilities


def test_capability_probe_observes_each_explicit_boolean_without_granting_writes() -> None:
    data = {
        "id": 12,
        "capabilities": {
            "edit_posts": True,
            "publish_posts": False,
            "upload_files": True,
            "assign_categories": True,
            "manage_categories": False,
            "edit_categories": False,
            "assign_post_tags": False,
            "manage_post_tags": False,
            "administrator": True,
        },
    }
    with patch(
        "bukmatika.plugins.wordpress_capability_probe.get_wordpress_rest",
        return_value=WordPressHttpResponse(200, json.dumps(data).encode()),
    ) as request:
        receipt = probe_wordpress_capabilities("https://example.com", "author", "private")
    assert receipt.state is ConnectionHealth.VERIFIED
    assert receipt.capabilities_observed
    assert receipt.capabilities["edit_posts"]
    assert not receipt.capabilities["publish_posts"]
    assert receipt.capabilities["upload_files"]
    assert "administrator" not in receipt.capabilities
    assert "private" not in repr(receipt)
    assert request.call_args.args == (
        "https://example.com",
        "/wp-json/wp/v2/users/me?context=edit",
    )
    assert request.call_args.kwargs["authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (WordPressHttpResponse(401, b"{}"), "AUTH_CAPABILITY_REJECTED"),
        (WordPressHttpResponse(403, b"{}"), "AUTH_CAPABILITY_REJECTED"),
        (WordPressHttpResponse(200, b'{"id":12}'), "AUTH_CAPABILITY_SCHEMA_INVALID"),
        (
            WordPressHttpResponse(200, b'{"id":12,"capabilities":{"edit_posts":"yes"}}'),
            "AUTH_CAPABILITY_SCHEMA_INVALID",
        ),
        (
            WordPressHttpResponse(200, b'{"id":12,"capabilities":{"edit_posts":1}}'),
            "AUTH_CAPABILITY_SCHEMA_INVALID",
        ),
        (WordPressHttpResponse(200, b"invalid"), "AUTH_CAPABILITY_RESPONSE_INVALID"),
        (WordPressHttpResponse(302, b"{}"), "AUTH_CAPABILITY_HTTP_UNEXPECTED"),
    ],
)
def test_untrusted_capability_response_always_fails_closed(
    response: WordPressHttpResponse, code: str
) -> None:
    with patch(
        "bukmatika.plugins.wordpress_capability_probe.get_wordpress_rest",
        return_value=response,
    ):
        receipt = probe_wordpress_capabilities("https://example.com", "author", "secret")
    assert receipt.state is ConnectionHealth.UNAVAILABLE
    assert not receipt.capabilities_observed
    assert not any(receipt.capabilities.values())
    assert receipt.evidence_code == code


@pytest.mark.parametrize(
    "route",
    [
        "/wp-json/wp/v2/posts?context=edit",
        "/wp-json/wp/v2/users/me?context=view",
        "/wp-json/wp/v2/users/me?context=edit&other=1",
        "/wp-json/wp/v2/users?context=edit",
    ],
)
def test_authorization_is_never_sent_to_unapproved_routes(route: str) -> None:
    with pytest.raises(ValueError):
        get_wordpress_rest(
            "https://example.com", route, authorization="Basic dXNlcjpwYXNz"
        )


def test_transport_failure_and_invalid_username_do_not_expose_credentials() -> None:
    with patch(
        "bukmatika.plugins.wordpress_capability_probe.get_wordpress_rest",
        side_effect=OSError("private password in remote failure"),
    ):
        receipt = probe_wordpress_capabilities("https://example.com", "author", "private")
    assert receipt.evidence_code == "AUTH_CAPABILITY_TRANSPORT_UNAVAILABLE"
    assert "private" not in repr(receipt)
    with patch("bukmatika.plugins.wordpress_capability_probe.get_wordpress_rest") as req:
        bad = probe_wordpress_capabilities("https://example.com", "bad:author", "private")
    assert bad.evidence_code == "AUTH_CAPABILITY_INPUT_INVALID"
    req.assert_not_called()
