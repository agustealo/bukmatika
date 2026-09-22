from dataclasses import dataclass
from enum import StrEnum

from bukmatika.ai.domain import CapabilityName, PlanProposal
from bukmatika.personalization.domain import ContextManifest


class CapabilityRisk(StrEnum):
    READ_ONLY = "read_only"
    DURABLE_WRITE = "durable_write"
    CONSEQUENTIAL = "consequential"
    PROPOSAL = "proposal"


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: CapabilityName
    risk: CapabilityRisk
    reversible: bool
    description: str


class PlanCapabilityUnavailable(ValueError):
    code = "PLAN_CAPABILITY_UNAVAILABLE"


class CapabilityRegistry:
    """Finite registry of product capabilities that future AI may reference."""

    def __init__(self, specs: tuple[CapabilitySpec, ...] | None = None) -> None:
        values = specs or _DEFAULT_CAPABILITIES
        self._specs = {spec.name: spec for spec in values}
        if len(self._specs) != len(values):
            raise ValueError("Capability names must be unique")

    def get(self, name: CapabilityName) -> CapabilitySpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise PlanCapabilityUnavailable(f"Capability is not registered: {name.value}") from exc

    def validate_plan(self, proposal: PlanProposal, context: ContextManifest) -> None:
        available = set(context.available_capabilities)
        for step in proposal.steps:
            self.get(step.capability)
            if step.capability.value not in available:
                raise PlanCapabilityUnavailable(
                    f"Capability is not available in this context: {step.capability.value}"
                )


_DEFAULT_CAPABILITIES = (
    CapabilitySpec(
        CapabilityName.DISCOVERY_SEARCH,
        CapabilityRisk.READ_ONLY,
        True,
        "Search configured discovery providers through the canonical discovery service.",
    ),
    CapabilitySpec(
        CapabilityName.CATALOG_SEARCH,
        CapabilityRisk.READ_ONLY,
        True,
        "Search the canonical catalog without mutating it.",
    ),
    CapabilitySpec(
        CapabilityName.LIBRARY_LIST,
        CapabilityRisk.READ_ONLY,
        True,
        "Inspect the authenticated principal's library.",
    ),
    CapabilitySpec(
        CapabilityName.LIBRARY_SAVE,
        CapabilityRisk.DURABLE_WRITE,
        True,
        "Save a canonical work or edition to the authenticated principal's library.",
    ),
    CapabilitySpec(
        CapabilityName.READER_OPEN,
        CapabilityRisk.READ_ONLY,
        True,
        "Open an owned processed document at a canonical reader location.",
    ),
    CapabilitySpec(
        CapabilityName.RESEARCH_SEARCH,
        CapabilityRisk.READ_ONLY,
        True,
        "Search selected owned books using the canonical research service.",
    ),
    CapabilitySpec(
        CapabilityName.RESEARCH_ANSWER,
        CapabilityRisk.READ_ONLY,
        True,
        (
            "Synthesize a citation-grounded answer from a finite canonical research evidence "
            "bundle. Registered but unavailable until a real ModelGateway provider is configured."
        ),
    ),
    CapabilitySpec(
        CapabilityName.ACQUISITION_REQUEST,
        CapabilityRisk.CONSEQUENTIAL,
        False,
        "Request lawful acquisition of an exact asset through the acquisition authority.",
    ),
    CapabilitySpec(
        CapabilityName.PREFERENCES_PROPOSE,
        CapabilityRisk.PROPOSAL,
        True,
        "Propose a personalization change for explicit user approval.",
    ),
    CapabilitySpec(
        CapabilityName.GOALS_UPDATE,
        CapabilityRisk.DURABLE_WRITE,
        True,
        "Propose an update to a durable user goal.",
    ),
)
