from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import httpx

from bukmatika.ai.embedding_gateway import EmbeddingGateway
from bukmatika.ai.gateway import ModelGateway
from bukmatika.config import Settings


class RoutingType(StrEnum):
    LOCAL = "local"
    CLOUD = "cloud"


class ModelCapability(StrEnum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_GENERATION = "structured_generation"
    STREAMING = "streaming"
    EMBEDDINGS = "embeddings"
    TOOL_CALLING = "tool_calling"
    VISION = "vision"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    display_name: str
    routing_type: RoutingType

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be empty")
        if not self.display_name.strip():
            raise ValueError("display_name must not be empty")


@dataclass(frozen=True, slots=True)
class ModelDescriptor:
    provider_id: str
    model_id: str
    capabilities: frozenset[ModelCapability]
    context_window: int | None = None
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_id must not be empty")
        if not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window must be positive")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")


class ProviderAdapter(Protocol):
    @property
    def descriptor(self) -> ProviderDescriptor: ...

    def describe_model(self, model_id: str) -> ModelDescriptor: ...

    def build_model_gateway(
        self,
        *,
        settings: Settings,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> ModelGateway: ...

    def build_embedding_gateway(
        self,
        *,
        settings: Settings,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> EmbeddingGateway: ...


class ProviderNotRegistered(LookupError):
    pass


class ProviderCapabilityUnavailable(RuntimeError):
    pass


class DuplicateProviderRegistration(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    descriptor: ProviderDescriptor
    describe_model: Callable[[str], ModelDescriptor]
    model_gateway_factory: Callable[[Settings, httpx.AsyncClient, str], ModelGateway] | None = None
    embedding_gateway_factory: Callable[[Settings, httpx.AsyncClient, str], EmbeddingGateway]
    | None = None


class ProviderRegistry:
    """Canonical provider discovery/construction authority.

    Registration is side-effect free. Providers are not probed or initialized until a concrete
    gateway is requested for an explicit provider/model selection.
    """

    def __init__(self) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(self, registration: ProviderRegistration) -> None:
        provider_id = registration.descriptor.provider_id
        if provider_id in self._registrations:
            raise DuplicateProviderRegistration(
                f"Provider is already registered: {provider_id}"
            )
        self._registrations[provider_id] = registration

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(
            registration.descriptor
            for _, registration in sorted(self._registrations.items())
        )

    def descriptor(self, provider_id: str) -> ProviderDescriptor:
        return self._registration(provider_id).descriptor

    def describe_model(self, *, provider_id: str, model_id: str) -> ModelDescriptor:
        descriptor = self._registration(provider_id).describe_model(model_id)
        if descriptor.provider_id != provider_id:
            raise ValueError("Provider returned a model descriptor for another provider")
        return descriptor

    def require_capability(
        self,
        *,
        provider_id: str,
        model_id: str,
        capability: ModelCapability,
    ) -> ModelDescriptor:
        descriptor = self.describe_model(provider_id=provider_id, model_id=model_id)
        if capability not in descriptor.capabilities:
            raise ProviderCapabilityUnavailable(
                f"Model {provider_id}/{model_id} does not support {capability.value}"
            )
        return descriptor

    def build_model_gateway(
        self,
        *,
        provider_id: str,
        model_id: str,
        settings: Settings,
        client: httpx.AsyncClient,
    ) -> ModelGateway:
        self.require_capability(
            provider_id=provider_id,
            model_id=model_id,
            capability=ModelCapability.STRUCTURED_GENERATION,
        )
        registration = self._registration(provider_id)
        if registration.model_gateway_factory is None:
            raise ProviderCapabilityUnavailable(
                f"Provider {provider_id} has no generation gateway"
            )
        return registration.model_gateway_factory(settings, client, model_id)

    def build_embedding_gateway(
        self,
        *,
        provider_id: str,
        model_id: str,
        settings: Settings,
        client: httpx.AsyncClient,
    ) -> EmbeddingGateway:
        self.require_capability(
            provider_id=provider_id,
            model_id=model_id,
            capability=ModelCapability.EMBEDDINGS,
        )
        registration = self._registration(provider_id)
        if registration.embedding_gateway_factory is None:
            raise ProviderCapabilityUnavailable(
                f"Provider {provider_id} has no embedding gateway"
            )
        return registration.embedding_gateway_factory(settings, client, model_id)

    def _registration(self, provider_id: str) -> ProviderRegistration:
        registration = self._registrations.get(provider_id)
        if registration is None:
            raise ProviderNotRegistered(f"Unknown model provider: {provider_id}")
        return registration
