from dataclasses import dataclass

from bukmatika.discovery.base import DiscoveryAdapter


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    adapter: DiscoveryAdapter
    max_results: int
    timeout_seconds: float
    enabled: bool = True

    @property
    def name(self) -> str:
        return self.adapter.name


class ProviderRegistry:
    """Single authority for enabled discovery providers and their budgets."""

    def __init__(self, registrations: list[ProviderRegistration]) -> None:
        names: set[str] = set()
        active = 0
        for registration in registrations:
            if registration.max_results < 1:
                raise ValueError("provider max_results must be positive")
            if registration.timeout_seconds <= 0:
                raise ValueError("provider timeout_seconds must be positive")
            if registration.name in names:
                raise ValueError(f"duplicate discovery provider: {registration.name}")
            names.add(registration.name)
            if registration.enabled:
                active += 1
        if active == 0:
            raise ValueError("at least one discovery provider must be enabled")
        self._registrations = tuple(registrations)

    def active(self) -> tuple[ProviderRegistration, ...]:
        return tuple(item for item in self._registrations if item.enabled)

    def names(self) -> list[str]:
        return [item.name for item in self.active()]
