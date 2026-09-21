import socket

import pytest

from bukmatika.acquisition.network import UnsafeRemoteURL, resolve_public_https


async def test_non_https_and_private_ip_literals_are_rejected() -> None:
    with pytest.raises(UnsafeRemoteURL):
        await resolve_public_https("http://example.org/book.pdf")
    with pytest.raises(UnsafeRemoteURL):
        await resolve_public_https("https://127.0.0.1/book.pdf")
    with pytest.raises(UnsafeRemoteURL):
        await resolve_public_https("https://10.0.0.8/book.pdf")
    with pytest.raises(UnsafeRemoteURL):
        await resolve_public_https("https://[::1]/book.pdf")


async def test_credential_bearing_url_is_rejected() -> None:
    with pytest.raises(UnsafeRemoteURL):
        await resolve_public_https("https://user:secret@example.org/book.pdf")


async def test_mixed_public_private_dns_answer_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        family: socket.AddressFamily,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        assert host == "books.example.org"
        assert port == 443
        assert family == socket.AF_UNSPEC
        assert type == socket.SOCK_STREAM
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeRemoteURL, match="non-public"):
        await resolve_public_https("https://books.example.org/book.pdf")


async def test_public_dns_answer_is_pinned_to_validated_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(
        host: str,
        port: int,
        *,
        family: socket.AddressFamily,
        type: socket.SocketKind,
    ) -> list[tuple[socket.AddressFamily, socket.SocketKind, int, str, tuple[str, int]]]:
        assert host == "books.example.org"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    target = await resolve_public_https("https://books.example.org/path/book.pdf?download=1")

    assert target.request_url == "https://93.184.216.34/path/book.pdf?download=1"
    assert target.original_url == "https://books.example.org/path/book.pdf?download=1"
    assert target.host_header == "books.example.org"
    assert target.sni_hostname == "books.example.org"
    assert target.resolved_ip == "93.184.216.34"
