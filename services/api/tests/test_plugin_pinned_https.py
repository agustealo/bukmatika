from unittest.mock import MagicMock, patch

import pytest

from bukmatika.plugins.pinned_https import get_wordpress_rest
from bukmatika.plugins.transport_policy import ApprovedHttpsDestination
from bukmatika.plugins.url_safety import UnsafePluginDestination


def _approved() -> ApprovedHttpsDestination:
    return ApprovedHttpsDestination(
        origin="https://example.com",
        hostname="example.com",
        addresses=("8.8.8.8",),
    )


def test_https_get_uses_pinned_ip_and_original_tls_hostname() -> None:
    with (
        patch(
            "bukmatika.plugins.pinned_https.approve_https_destination", return_value=_approved()
        ),
        patch("bukmatika.plugins.pinned_https.PinnedHttpsConnection") as conn_cls,
    ):
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok":true}'
        conn_cls.return_value.getresponse.return_value = response
        result = get_wordpress_rest("https://example.com")
    conn_cls.assert_called_once_with("example.com", "8.8.8.8", timeout=5.0)
    assert result.status_code == 200
    assert result.body == b'{"ok":true}'
    conn_cls.return_value.close.assert_called_once()


def test_redirects_are_rejected_without_following() -> None:
    with (
        patch("bukmatika.plugins.pinned_https.approve_https_destination", return_value=_approved()),
        patch("bukmatika.plugins.pinned_https.PinnedHttpsConnection") as conn_cls,
    ):
        conn_cls.return_value.getresponse.return_value = MagicMock(status=302)
        with pytest.raises(UnsafePluginDestination):
            get_wordpress_rest("https://example.com")
    conn_cls.return_value.close.assert_called_once()


@pytest.mark.parametrize(
    "route", ["https://other.test", "/other", "/wp-json/wp/v2/../users"]
)
def test_only_wordpress_rest_routes_allowed(route: str) -> None:
    with pytest.raises(ValueError):
        get_wordpress_rest("https://example.com", route)
