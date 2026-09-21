import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit


class UnsafeRemoteURL(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PinnedTarget:
    original_url: str
    request_url: str
    host_header: str
    sni_hostname: str
    resolved_ip: str


async def resolve_public_https(url: str) -> PinnedTarget:
    return await asyncio.to_thread(_resolve_public_https_sync, url)


def _resolve_public_https_sync(url: str) -> PinnedTarget:
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise UnsafeRemoteURL("Only HTTPS acquisition URLs are permitted")
    if parts.username is not None or parts.password is not None:
        raise UnsafeRemoteURL("Credential-bearing acquisition URLs are not permitted")
    if parts.hostname is None:
        raise UnsafeRemoteURL("Acquisition URL has no hostname")
    try:
        port = parts.port or 443
    except ValueError as exc:
        raise UnsafeRemoteURL("Acquisition URL has an invalid port") from exc
    if port != 443:
        raise UnsafeRemoteURL("Only the standard HTTPS port is permitted")

    host = parts.hostname.rstrip(".")
    if not host:
        raise UnsafeRemoteURL("Acquisition URL has an empty hostname")
    if "%" in host:
        raise UnsafeRemoteURL("IPv6 zone identifiers are not permitted")
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeRemoteURL("Acquisition hostname is not valid IDNA") from exc

    addresses = _resolve_addresses(ascii_host, port)
    unsafe = [address for address in addresses if not address.is_global]
    if unsafe:
        rendered = ", ".join(str(address) for address in unsafe)
        raise UnsafeRemoteURL(f"Acquisition hostname resolves to a non-public address: {rendered}")
    if not addresses:
        raise UnsafeRemoteURL("Acquisition hostname did not resolve to a usable address")

    resolved = addresses[0]
    request_parts = SplitResult(
        scheme="https",
        netloc=_netloc(str(resolved), resolved.version == 6),
        path=parts.path or "/",
        query=parts.query,
        fragment="",
    )
    original_parts = SplitResult(
        scheme="https",
        netloc=_netloc(ascii_host, ":" in ascii_host),
        path=parts.path or "/",
        query=parts.query,
        fragment="",
    )
    return PinnedTarget(
        original_url=urlunsplit(original_parts),
        request_url=urlunsplit(request_parts),
        host_header=_netloc(ascii_host, ":" in ascii_host),
        sni_hostname=ascii_host,
        resolved_ip=str(resolved),
    )


def _resolve_addresses(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return [literal]

    try:
        records = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeRemoteURL("Acquisition hostname could not be resolved") from exc

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    seen: set[str] = set()
    for record in records:
        raw = str(record[4][0]).split("%", 1)[0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            continue
        rendered = str(address)
        if rendered not in seen:
            seen.add(rendered)
            addresses.append(address)
    return addresses


def _netloc(host: str, ipv6: bool) -> str:
    return f"[{host}]" if ipv6 else host
