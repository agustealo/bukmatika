from unittest.mock import MagicMock, patch

import pytest

from bukmatika.plugins.pinned_https import PinnedHttpsConnection, get_wordpress_rest
from bukmatika.plugins.transport_policy import ApprovedHttpsDestination


def test_tcp_socket_uses_pinned_ip_and_tls_keeps_original_hostname() -> None:
    raw = MagicMock()
    tls = MagicMock()
    with (
        patch("bukmatika.plugins.pinned_https.ssl.create_default_context") as tls_factory,
        patch("bukmatika.plugins.pinned_https.socket.create_connection") as tcp,
    ):
        tls_factory.return_value.wrap_socket.return_value = tls
        tcp.return_value = raw
        conn = PinnedHttpsConnection("example.com", "8.8.8.8", timeout=3.0)
        conn.connect()
    tcp.assert_called_once_with(("8.8.8.8", 443), 3.0)
    tls_factory.return_value.wrap_socket.assert_called_once_with(
        raw, server_hostname="example.com"
    )
    assert conn.sock is tls


def test_failed_tls_handshake_closes_raw_socket() -> None:
    raw = MagicMock()
    with (
        patch("bukmatika.plugins.pinned_https.ssl.create_default_context") as tls_factory,
        patch("bukmatika.plugins.pinned_https.socket.create_connection", return_value=raw),
    ):
        tls_factory.return_value.wrap_socket.side_effect = OSError("bad certificate")
        conn = PinnedHttpsConnection("example.com", "8.8.8.8")
        with pytest.raises(OSError):
            conn.connect()
    raw.close.assert_called_once()


def test_response_size_limit_fails_closed_and_closes_connection() -> None:
    approved = ApprovedHttpsDestination(
        origin="https://example.com",
        hostname="example.com",
        addresses=("8.8.8.8",),
    )
    with (
        patch(
            "bukmatika.plugins.pinned_https.approve_https_destination",
            return_value=approved,
        ),
        patch("bukmatika.plugins.pinned_https.PinnedHttpsConnection") as conn_cls,
    ):
        conn_cls.return_value.getresponse.return_value = MagicMock(
            status=200,
            read=MagicMock(return_value=b"12345"),
        )
        with pytest.raises(ValueError, match="exceeds maximum"):
            get_wordpress_rest("https://example.com", max_bytes=4)
    conn_cls.return_value.close.assert_called_once()
