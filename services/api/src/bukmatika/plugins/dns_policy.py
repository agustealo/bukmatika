"""DNS admission policy for future pinned WordPress transport.

Resolving is performed separately from opening sockets. A caller MUST pin the
validated addresses at connect time, disable redirects and environment proxies,
and revalidate on every new connection, rather than re-resolving unsafely.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from bukmatika.plugins.url_safety import UnsafePluginDestination, validate_public_https_url

Resolver = Callable[[str, int], list[tuple[object, ...]]]


def resolve_public_destination(
    site_url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Return validated IP addresses, never an unsafe partial DNS answer."""
    validate_public_https_url(site_url)
    host = urlsplit(site_url).hostname
    if host is None:
        raise UnsafePluginDestination("Destination host is required")
    try:
        answers = resolver(host, 443)
    except (OSError, ValueError) as exc:
        raise UnsafePluginDestination("Destination DNS resolution failed") from exc
    if not answers:
        raise UnsafePluginDestination("Destination DNS returned no addresses")
    addresses: set[str] = set()
    try:
        for answer in answers:
            ip = ipaddress.ip_address(str(answer[4][0]))
            if not ip.is_global:
                raise UnsafePluginDestination("Destination DNS contains non-public IP")
            addresses.add(str(ip))
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise UnsafePluginDestination("Destination DNS response is invalid") from exc
    return tuple(sorted(addresses))
