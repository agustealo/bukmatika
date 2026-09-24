from typing import Protocol

from pydantic import BaseModel, JsonValue, ValidationError

from bukmatika.ai.domain import CapabilityName
from bukmatika.personalization.domain import ContextManifest
from bukmatika.research.domain import ResearchSearchRequest


class CapabilityArgumentsInvalid(ValueError):
    code = "CAPABILITY_ARGUMENTS_INVALID"


class CapabilityContractUnavailable(RuntimeError):
    code = "CAPABILITY_CONTRACT_UNAVAILABLE"


class CapabilityArgumentContract(Protocol):
    capability: CapabilityName

    def validate(
        self,
        *,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel: ...


class ResearchSearchArgumentContract:
    capability = CapabilityName.RESEARCH_SEARCH

    def validate(
        self,
        *,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> ResearchSearchRequest:
        try:
            request = ResearchSearchRequest.model_validate(arguments)
        except ValidationError as exc:
            raise CapabilityArgumentsInvalid("Invalid research.search arguments") from exc

        selected_entry_ids = {entry.library_entry_id for entry in context.library_entries}
        requested_entry_ids = set(request.library_entry_ids)
        if not requested_entry_ids.issubset(selected_entry_ids):
            raise CapabilityArgumentsInvalid(
                "research.search library entries must be selected in the persisted context"
            )
        return request


class CapabilityArgumentContractRegistry:
    """Finite argument/context contracts shared by preflight and runtime execution."""

    def __init__(
        self,
        contracts: tuple[CapabilityArgumentContract, ...] | None = None,
    ) -> None:
        values = contracts or (ResearchSearchArgumentContract(),)
        self._contracts = {contract.capability: contract for contract in values}
        if len(self._contracts) != len(values):
            raise ValueError("Capability argument contract names must be unique")

    def validate(
        self,
        *,
        capability: CapabilityName,
        arguments: dict[str, JsonValue],
        context: ContextManifest,
    ) -> BaseModel:
        try:
            contract = self._contracts[capability]
        except KeyError as exc:
            raise CapabilityContractUnavailable(
                f"No argument contract is registered for capability: {capability.value}"
            ) from exc
        return contract.validate(arguments=arguments, context=context)
