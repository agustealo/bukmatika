from dataclasses import dataclass
from enum import StrEnum


class ConnectionFieldKind(StrEnum):
    TEXT = "text"
    URL = "url"
    INTEGER = "integer"
    SECRET = "secret"


@dataclass(frozen=True, slots=True)
class ConnectionField:
    key: str
    label: str
    kind: ConnectionFieldKind
    required: bool = True

    def __post_init__(self) -> None:
        if not self.key or not self.key.replace("_", "").isalnum():
            raise ValueError("Connection field key must be an identifier")
        if not self.label.strip():
            raise ValueError("Connection field label must not be empty")


@dataclass(frozen=True, slots=True)
class ConnectionMode:
    mode_id: str
    display_name: str
    fields: tuple[ConnectionField, ...]

    def __post_init__(self) -> None:
        if not self.mode_id or not self.mode_id.replace("_", "").isalnum():
            raise ValueError("Connection mode ID must be an identifier")
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError("Duplicate connection field")
        if not self.display_name.strip():
            raise ValueError("Connection mode display name must not be empty")


@dataclass(frozen=True, slots=True)
class SourceConnectionContract:
    schema_version: int
    modes: tuple[ConnectionMode, ...]

    def __post_init__(self) -> None:
        if self.schema_version < 1 or not self.modes:
            raise ValueError("A versioned connection contract requires a mode")
        if len({mode.mode_id for mode in self.modes}) != len(self.modes):
            raise ValueError("Duplicate connection mode")
