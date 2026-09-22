from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from bukmatika.ai.domain import CapabilityRisk
from bukmatika.domain import SearchIntent
from bukmatika.personalization.domain import ContextTask


class NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=24, ge=1, le=100)


class ReaderOpenArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    library_entry_id: UUID
    document_id: UUID
    section_ordinal: int | None = Field(default=None, ge=0)


class ResearchSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    library_entry_ids: list[UUID] = Field(min_length=1, max_length=20)
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=24, ge=1, le=100)


class AcquisitionRequestArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: UUID


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: str
    risk: CapabilityRisk
    argument_model: type[BaseModel]

    def validate_arguments(self, arguments: dict[str, object]) -> dict[str, object]:
        validated = self.argument_model.model_validate(arguments)
        return validated.model_dump(mode="json")


class CapabilityRegistry:
    """Single registry for capabilities an AI plan may reference."""

    def __init__(self) -> None:
        specs = (
            CapabilitySpec(
                name="discovery.search",
                risk=CapabilityRisk.NETWORK_READ,
                argument_model=SearchIntent,
            ),
            CapabilitySpec(
                name="catalog.search",
                risk=CapabilityRisk.READ_ONLY,
                argument_model=CatalogSearchArguments,
            ),
            CapabilitySpec(
                name="library.list",
                risk=CapabilityRisk.READ_ONLY,
                argument_model=NoArguments,
            ),
            CapabilitySpec(
                name="reader.open",
                risk=CapabilityRisk.READ_ONLY,
                argument_model=ReaderOpenArguments,
            ),
            CapabilitySpec(
                name="research.search",
                risk=CapabilityRisk.READ_ONLY,
                argument_model=ResearchSearchArguments,
            ),
            CapabilitySpec(
                name="acquisition.request",
                risk=CapabilityRisk.CONSEQUENTIAL,
                argument_model=AcquisitionRequestArguments,
            ),
        )
        self._specs = {spec.name: spec for spec in specs}
        self._task_capabilities: dict[ContextTask, tuple[str, ...]] = {
            ContextTask.DISCOVERY: ("discovery.search", "catalog.search"),
            ContextTask.LIBRARY: ("library.list", "reader.open"),
            ContextTask.RESEARCH: ("research.search", "reader.open"),
            ContextTask.READER: ("reader.open", "research.search"),
        }

    def get(self, name: str) -> CapabilitySpec | None:
        return self._specs.get(name)

    def require(self, name: str) -> CapabilitySpec:
        spec = self.get(name)
        if spec is None:
            raise KeyError(name)
        return spec

    def for_task(self, task: ContextTask) -> tuple[CapabilitySpec, ...]:
        return tuple(self.require(name) for name in self._task_capabilities[task])

    def names_for_task(self, task: ContextTask) -> tuple[str, ...]:
        return tuple(spec.name for spec in self.for_task(task))
