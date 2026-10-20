"""Conservative storage-time URL rules, not a substitute for connect-time DNS pinning."""

import ipaddress
from urllib.parse import urlsplit


class UnsafePluginDestination(ValueError):
    pass


def validate_public_https_url(value: str) -> str:
    if value != value.strip() or any(ord(char) < 32 for char in value):
        raise UnsafePluginDestination("Invalid destination URL")
    try:
        parts = urlsplit(value)
        port = parts.port
        host = parts.hostname
    except ValueError as exc:
        raise UnsafePluginDestination("Invalid destination URL") from exc
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or parts.query
        or parts.path not in ("", "/")
        or port not in (None, 443)
    ):
        raise UnsafePluginDestination("Only HTTPS site origins are accepted")
    if host.endswith(".") or "%" in host or "_" in host or host.lower() == "localhost":
        raise UnsafePluginDestination("Destination host is not public")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2 or any(not label or len(label) > 63 for label in labels):
            raise UnsafePluginDestination("Destination must use a public DNS name")
        if not all(\n            label.isascii()\n            and label.replace("-", "").isalnum()\n            and not label.startswith("-")\n            and not label.endswith("-")\n            for label in labels\n        ):
            raise UnsafePluginDestination("Destination host is invalid")
        if labels[-1].lower() in {"local", "internal", "localhost", "test", "invalid"}:
            raise UnsafePluginDestination("Destination host is reserved")
    else:
        raise UnsafePluginDestination("Literal IP destinations are not accepted")
    return value.rstrip("/")
