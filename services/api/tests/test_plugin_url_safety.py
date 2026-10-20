import pytest

from bukmatika.plugins.url_safety import UnsafePluginDestination, validate_public_https_url


@pytest.mark.parametrize(
    "value",
    [
        "http://example.com",
        "https://localhost",
        "https://localhost.local",
        "https://127.0.0.1",
        "https://[::1]",
        "https://10.1.1.1",
        "https://example.com:8443",
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?token=secret",
        "https://example.com#fragment",
        "https://host.internal",
        "https://host.local",
        "https://example.com@localhost",
        "https://example.com\\@localhost",
        "https://example.com.",
    ],
)
def test_rejects_unsafe_or_non_origin_destinations(value: str) -> None:
    with pytest.raises(UnsafePluginDestination):
        validate_public_https_url(value)


def test_accepts_public_https_origin_only() -> None:
    assert validate_public_https_url("https://example.com/") == "https://example.com"
