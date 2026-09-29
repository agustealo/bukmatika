import httpx

from bukmatika.ai.embedding_gateway import EmbeddingGateway, UnconfiguredEmbeddingGateway
from bukmatika.ai.gateway import ModelGateway, UnconfiguredModelGateway
from bukmatika.ai.ollama_provider import ollama_registration
from bukmatika.ai.provider_registry import ProviderRegistry
from bukmatika.config import Settings


def build_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(ollama_registration())
    return registry


def build_model_gateway(
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> ModelGateway:
    """Construct the installation-default model gateway."""
    return build_selected_model_gateway(
        settings=settings,
        client=client,
        provider=settings.model_provider,
        model=settings.ollama_model,
    )


def build_selected_model_gateway(
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    provider: str,
    model: str | None,
    registry: ProviderRegistry | None = None,
) -> ModelGateway:
    """Construct a generation gateway through the canonical provider registry."""
    if provider == "none":
        return UnconfiguredModelGateway()
    normalized_model = (model or "").strip()
    if not normalized_model:
        return UnconfiguredModelGateway()
    selected_registry = registry or build_provider_registry()
    return selected_registry.build_model_gateway(
        provider_id=provider,
        model_id=normalized_model,
        settings=settings,
        client=client,
    )


def build_embedding_gateway(
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> EmbeddingGateway:
    """Construct the installation-default embedding gateway, fail-closed when unconfigured."""
    return build_selected_embedding_gateway(
        settings=settings,
        client=client,
        provider=settings.embedding_provider,
        model=settings.ollama_embedding_model,
    )


def build_selected_embedding_gateway(
    *,
    settings: Settings,
    client: httpx.AsyncClient,
    provider: str,
    model: str | None,
    registry: ProviderRegistry | None = None,
) -> EmbeddingGateway:
    if provider == "none":
        return UnconfiguredEmbeddingGateway()
    normalized_model = (model or "").strip()
    if not normalized_model:
        return UnconfiguredEmbeddingGateway()
    selected_registry = registry or build_provider_registry()
    return selected_registry.build_embedding_gateway(
        provider_id=provider,
        model_id=normalized_model,
        settings=settings,
        client=client,
    )


def model_provider_configured(gateway: ModelGateway) -> bool:
    return gateway.identity is not None
