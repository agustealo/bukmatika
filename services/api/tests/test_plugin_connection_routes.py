import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from bukmatika.plugins.connection_routes import (
    ConnectionConfiguration,
    ConnectionState,
    router,
)


def test_connection_routes_are_authenticated_and_explicit() -> None:
    methods = {
        (route.path, tuple(sorted(route.methods or set())))
        for route in router.routes
        if isinstance(route, APIRoute)
    }
    assert ("/v1/plugins/{plugin_id}/connection", ("PUT",)) in methods
    assert ("/v1/plugins/{plugin_id}/connection", ("GET",)) in methods
    for route in router.routes:
        assert route.dependant.dependencies


def test_connection_payload_disallows_unknown_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        ConnectionConfiguration(
            mode_id="rest",
            values={"site_url": "https://example.com"},
            arbitrary="not allowed",
        )


def test_connection_response_does_not_have_secret_field() -> None:
    assert "application_password" not in ConnectionState.model_fields
    assert "password" not in ConnectionState.model_fields
