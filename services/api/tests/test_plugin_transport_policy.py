import socket

import pytest

from bukmatika.plugins.transport_policy import (
    ApprovedHttpsDestination,
    approve_https_destination,
)
from bukmatika.plugins.url_safety import UnsafePluginDestination


def test_approved_https_destination_retains_hostname_and_addresses() -> None:
    destination = approve_https_destination(
        "https://example.com/",
        resolver=lambda host, port: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        ],
    )
    assert destination.hostname == "example.com"
    assert destination.origin == "https://example.com"
    assert destination.addresses == ("8.8.8.8",)
    assert destination.follow_redirects is False
    assert destination.trust_env is False


def test_mixed_address_answer_is_rejected() -> None:
    with pytest.raises(UnsafePluginDestination):
        approve_https_destination(
            "https://example.com",
            resolver=lambda host, port: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ],
        )


def test_unsafe_transport_options_are_rejected() -> None:
    with pytest.raises(ValueError):
        ApprovedHttpsDestination(
            origin="https://example.com",
            hostname="example.com",
            addresses=("8.8.8.8",),
            trust_env=True,
        )
