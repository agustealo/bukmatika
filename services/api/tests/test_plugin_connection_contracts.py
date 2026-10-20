import pytest

from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_contracts import (
    ConnectionField,
    ConnectionFieldKind,
    ConnectionMode,
    SourceConnectionContract,
)


def test_wordpress_owns_rest_and_mysql_connection_schemas() -> None:
    registration = build_builtin_plugin_registry().registration("wordpress")
    assert registration.manifest.requires_connection
    assert registration.connection_contract is not None
    modes = {mode.mode_id: mode for mode in registration.connection_contract.modes}
    assert set(modes) == {"rest", "mysql"}
    for mode in modes.values():
        assert any(field.kind is ConnectionFieldKind.SECRET for field in mode.fields)
    assert "application_password" in {field.key for field in modes["rest"].fields}
    assert "table_prefix" in {field.key for field in modes["mysql"].fields}


def test_bundled_public_discovery_needs_no_connection() -> None:
    registry = build_builtin_plugin_registry()
    for registration in registry.discovery_registrations():
        assert not registration.manifest.requires_connection
        assert registration.connection_contract is None


def test_contract_rejects_duplicate_modes_and_fields() -> None:
    field = ConnectionField("site_url", "Site URL", ConnectionFieldKind.URL)
    mode = ConnectionMode("rest", "REST", (field,))
    with pytest.raises(ValueError, match="Duplicate connection mode"):
        SourceConnectionContract(schema_version=1, modes=(mode, mode))
    with pytest.raises(ValueError, match="Duplicate connection field"):
        ConnectionMode("rest", "REST", (field, field))
