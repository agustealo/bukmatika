"""Explicit transport safety contract for future WordPress HTTPS requests.

Policy verification is not a socket connector. A transport implementation MUST
use the approved IP as the actual socket destination while preserving TLS SNI
and certificate hostname verification against the original DNS name.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit

from bukmatika.plugins.dns_policy import Resolver, resolve_public_destination
from bukmatika.plugins.url_safety import validate_public_https_url


@dataclass(frozen=True, slots=True)
class ApprovedHttpsDestination:
    origin: str
    hostname: str
    addresses: tuple[str, ...]
    port: int = 443
    follow_redirects: bool = False
    trust_env: bool = False

    def __post_init__(self) -> None:
        if not self.addresses or self.port != 443 or self.follow_redirects or self.trust_env:
            raise ValueError("Unsafe HTTPS destination policy")


def approve_https_destination(
    origin: str,
    *,
    resolver: Resolver | None = None,
) -> ApprovedHttpsDestination:
    normalized = validate_public_https_url(origin)
    hostname = urlsplit(normalized).hostname
    if hostname is None:
        raise ValueError("Missing HTTPS destination hostname")
    if resolver is None:
        addresses = resolve_public_destination(normalized)
    else:
        addresses = resolve_public_destination(normalized, resolver=resolver)
    return ApprovedHttpsDestination(
        origin=normalized,
        hostname=hostname,
        addresses=addresses,
    )
