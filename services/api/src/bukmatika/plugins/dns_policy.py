"""DNS admission policy for future pinned WordPress transport.

Resolving is performed separately from opening sockets. A caller MUST pin the
validated addresses at connect time, disable redirects and environment proxies,
and revalidate on every new connection, rather than re-resolving unsafely.
"""

import ipaddress
import socket
from collections.abc import Callable
from typing import cast
from urllib.parse import urlsplit

from bukmatika.plugins.url_safety import UnsafePluginDestination, validate_public_https_url

Resolver = Callable[[str, int], list[tuple[object, ...]]]


def resolve_public_destination(
    site_url: str,
    *,
    resolver: Resolver | None = None,
) -> tuple[str, ...]:
    """Return validated IP addresses, never an unsafe partial DNS answer."""
    validate_public_https_url(site_url)
    host = urlsplit(site_url).hostname
    if host is None:
        raise UnsafePluginDestination("Destination host is required")
    try:
        answers = (
            resolver(host, 443)
            if resolver is not None
            else cast(list[tuple[object, ...]], socket.getaddrinfo(host, 443))
        )
    except (OSError, ValueError) as exc:
        raise UnsafePluginDestination("Destination DNS resolution failed") from exc
    if not answers:
        raise UnsafePluginDestination("Destination DNS returned no addresses")
    addresses: set[str] = set()
    try:
        for answer in answers:
            sockaddr = answer[4]
            if not isinstance(sockaddr, tuple) or not sockaddr:
                raise UnsafePluginDestination("Destination DNS response is invalid")
            ip = ipaddress.ip_address(str(sockaddr[0]))
            if not ip.is_global:
                raise UnsafePluginDestination("Destination DNS contains non-public IP")
            addresses.add(str(ip))
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise UnsafePluginDestination("Destination DNS response is invalid") from exc
    return tuple(sorted(addresses))
