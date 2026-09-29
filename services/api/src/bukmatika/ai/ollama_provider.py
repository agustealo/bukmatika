import httpx

from bukmatika.ai.embedding_gateway import EmbeddingGateway
from bukmatika.ai.gateway import ModelGateway
from bukmatika.ai.ollama import OllamaLocalGateway
from bukmatika.ai.ollama_embedding import OllamaLocalEmbeddingGateway
from bukmatika.ai.provider_registry import (
    ModelCapability,
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRegistration,
    RoutingType,
)
from bukmatika.config import Settings


def _describe_model(model_id: str) -> ModelDescriptor:
    normalized = model_id.strip()
    if not normalized:
        raise ValueError("model_id must not be empty")
    return ModelDescriptor(
        provider_id="ollama",
        model_id=normalized,
        capabilities=frozenset(
            {
                ModelCapability.TEXT_GENERATION,
                ModelCapability.STRUCTURED_GENERATION,
                ModelCapability.EMBEDDINGS,
            }
        ),
    )


def _build_model_gateway(
    settings: Settings,
    client: httpx.AsyncClient,
    model_id: str,
) -> ModelGateway:
    return OllamaLocalGateway(
        client=client,
        base_url=settings.ollama_base_url,
        model=model_id,
        timeout_seconds=settings.model_timeout_seconds,
        readiness_timeout_seconds=settings.model_readiness_timeout_seconds,
    )


def _build_embedding_gateway(
    settings: Settings,
    client: httpx.AsyncClient,
    model_id: str,
) -> EmbeddingGateway:
    return OllamaLocalEmbeddingGateway(
        client=client,
        base_url=settings.ollama_base_url,
        model=model_id,
        timeout_seconds=settings.embedding_timeout_seconds,
        readiness_timeout_seconds=settings.model_readiness_timeout_seconds,
    )


def ollama_registration() -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="ollama",
            display_name="Ollama",
            routing_type=RoutingType.LOCAL,
        ),
        describe_model=_describe_model,
        model_gateway_factory=_build_model_gateway,
        embedding_gateway_factory=_build_embedding_gateway,
    )
