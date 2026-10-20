"""Restricted WordPress HTTPS GET transport.

This synchronous, isolated transport pins the socket to a policy-approved IP
while retaining the original hostname for TLS SNI and certificate verification.
It never follows HTTP redirects or trusts environment proxy configuration.
"""

import http.client
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import quote

from bukmatika.plugins.transport_policy import approve_https_destination
from bukmatika.plugins.url_safety import UnsafePluginDestination


@dataclass(frozen=True, slots=True)
class WordPressHttpResponse:
    status_code: int
    body: bytes


class PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, pinned_ip: str, *, timeout: float = 5.0) -> None:
        super().__init__(
            hostname,
            port=443,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_ip, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def get_wordpress_rest(
    origin: str,
    route: str = "/wp-json/wp/v2/types",
    *,
    timeout: float = 5.0,
    max_bytes: int = 1_048_576,
) -> WordPressHttpResponse:
    """Read one bounded response. Fresh DNS check on each invocation; no redirects."""
    if (
        not route.startswith("/wp-json/wp/v2/")
        or "//" in route
        or "?" in route
        or "#" in route
        or ".." in route
        or timeout <= 0
        or max_bytes < 1
    ):
        raise ValueError("Invalid WordPress REST request parameters")
    destination = approve_https_destination(origin)
    if len(destination.addresses) < 1:
        raise UnsafePluginDestination("No approved destination")
    # One pinned IP is used for the socket; the DNS name is never resolved by
    # http.client because PinnedHttpsConnection.connect overrides socket setup.
    connection = PinnedHttpsConnection(destination.hostname, destination.addresses[0], timeout=timeout)
    try:
        connection.request(
            "GET",
            quote(route, safe="/%-._~"),
            headers={"Accept": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise UnsafePluginDestination("WordPress redirect is prohibited")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("WordPress response exceeds maximum size")
        return WordPressHttpResponse(status_code=response.status, body=body)
    finally:
        connection.close()
