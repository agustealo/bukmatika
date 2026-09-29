from collections.abc import Callable
from typing import cast

import httpx
import pytest

from bukmatika.ai.embedding_gateway import UnconfiguredEmbeddingGateway
from bukmatika.ai.gateway import UnconfiguredModelGateway
from bukmatika.ai.provider_registry import (
    DuplicateProviderRegistration,
    ModelCapability,
    ModelDescriptor,
    ProviderCapabilityUnavailable,
    ProviderDescriptor,
    ProviderNotRegistered,
    ProviderRegistration,
    ProviderRegistry,
    RoutingType,
)
from bukmatika.config import Settings


def registration(
    provider_id: str,
    *,
    capabilities: frozenset[ModelCapability],
    on_model_factory: Callable[[], None] | None = None,
) -> ProviderRegistration:
    def describe_model(model_id: str) -> ModelDescriptor:
        return ModelDescriptor(
            provider_id=provider_id,
            model_id=model_id,
            capabilities=capabilities,
        )

    def build_model_gateway(
        settings: Settings,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> UnconfiguredModelGateway:
        del settings, client, model_id
        if on_model_factory is not None:
            on_model_factory()
        return UnconfiguredModelGateway()

    def build_embedding_gateway(
        settings: Settings,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> UnconfiguredEmbeddingGateway:
        del settings, client, model_id
        return UnconfiguredEmbeddingGateway()

    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id=provider_id,
            display_name=provider_id.title(),
            routing_type=RoutingType.LOCAL,
        ),
        describe_model=describe_model,
        model_gateway_factory=build_model_gateway,
        embedding_gateway_factory=build_embedding_gateway,
    )


def test_registry_rejects_duplicate_and_unknown_providers() -> None:
    registry = ProviderRegistry()
    registered = registration(
        "synthetic",
        capabilities=frozenset({ModelCapability.STRUCTURED_GENERATION}),
    )
    registry.register(registered)

    with pytest.raises(DuplicateProviderRegistration):
        registry.register(registered)

    with pytest.raises(ProviderNotRegistered):
        registry.descriptor("missing")


def test_registration_is_side_effect_free_and_capability_check_precedes_factory() -> None:
    factory_calls = 0

    def record_factory_call() -> None:
        nonlocal factory_calls
        factory_calls += 1

    registry = ProviderRegistry()
    registry.register(
        registration(
            "embedding-only",
            capabilities=frozenset({ModelCapability.EMBEDDINGS}),
            on_model_factory=record_factory_call,
        )
    )
    assert factory_calls == 0

    with pytest.raises(ProviderCapabilityUnavailable):
        registry.build_model_gateway(
            provider_id="embedding-only",
            model_id="embed-v1",
            settings=Settings(),
            client=cast(httpx.AsyncClient, object()),
        )

    assert factory_calls == 0


def test_second_provider_uses_same_registry_without_core_changes() -> None:
    first_calls = 0
    second_calls = 0

    def first_called() -> None:
        nonlocal first_calls
        first_calls += 1

    def second_called() -> None:
        nonlocal second_calls
        second_calls += 1

    registry = ProviderRegistry()
    registry.register(
        registration(
            "first",
            capabilities=frozenset({ModelCapability.STRUCTURED_GENERATION}),
            on_model_factory=first_called,
        )
    )
    registry.register(
        registration(
            "second",
            capabilities=frozenset({ModelCapability.STRUCTURED_GENERATION}),
            on_model_factory=second_called,
        )
    )

    gateway = registry.build_model_gateway(
        provider_id="second",
        model_id="reasoner-v1",
        settings=Settings(),
        client=cast(httpx.AsyncClient, object()),
    )

    assert isinstance(gateway, UnconfiguredModelGateway)
    assert first_calls == 0
    assert second_calls == 1
    assert [descriptor.provider_id for descriptor in registry.descriptors()] == [
        "first",
        "second",
    ]
