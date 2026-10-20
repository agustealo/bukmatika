import socket

import pytest

from bukmatika.plugins.dns_policy import resolve_public_destination
from bukmatika.plugins.url_safety import UnsafePluginDestination


def _answers(*addresses: str) -> list[tuple[object, ...]]:
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        for address in addresses
    ]


def test_public_dns_addresses_can_be_validated_without_opening_socket() -> None:
    result = resolve_public_destination(
        "https://example.com",
        resolver=lambda host, port: _answers("8.8.8.8", "1.1.1.1"),
    )
    assert result == ("1.1.1.1", "8.8.8.8")


@pytest.mark.parametrize(
    "addresses",
    [
        ("127.0.0.1",),
        ("10.0.0.1",),
        ("169.254.169.254",),
        ("192.168.0.1",),
        ("8.8.8.8", "127.0.0.1"),
        ("::1",),
        ("fc00::1",),
    ],
)
def test_all_dns_results_must_be_public(addresses: tuple[str, ...]) -> None:
    with pytest.raises(UnsafePluginDestination):
        resolve_public_destination(
            "https://example.com",
            resolver=lambda host, port: _answers(*addresses),
        )


def test_dns_failure_and_empty_results_fail_closed() -> None:
    with pytest.raises(UnsafePluginDestination):
        resolve_public_destination(
            "https://example.com",
            resolver=lambda host, port: [],
        )

    def broken(host: str, port: int) -> list[tuple[object, ...]]:
        raise OSError("dns unavailable")

    with pytest.raises(UnsafePluginDestination):
        resolve_public_destination("https://example.com", resolver=broken)
