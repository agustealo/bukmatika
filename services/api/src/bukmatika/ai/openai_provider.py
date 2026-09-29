import httpx

from bukmatika.ai.gateway import ModelGateway
from bukmatika.ai.openai import OpenAICloudGateway
from bukmatika.ai.provider_registry import (
    ModelCapability,
    ModelDescriptor,
    ProviderCapabilityUnavailable,
    ProviderDescriptor,
    ProviderRegistration,
    RoutingType,
)
from bukmatika.config import Settings

# Conservative provider contract verified against OpenAI's model catalog on 2026-09-29.
# Expanding this set requires capability verification rather than assuming every model alias
# supports Bukmatika's strict Structured Outputs contract.
_SUPPORTED_MODELS: dict[str, tuple[int, int]] = {
    "gpt-6-astra": (1_050_000, 128_000),
    "gpt-6.1-sol": (1_050_000, 128_000),
    "gpt-6-luna": (1_050_000, 128_000),
}


def _describe_model(model_id: str) -> ModelDescriptor:
    normalized = model_id.strip()
    limits = _SUPPORTED_MODELS.get(normalized)
    if limits is None:
        raise ProviderCapabilityUnavailable(
            f"OpenAI model is not approved for Bukmatika structured generation: {normalized or '<blank>'}"
        )
    context_window, max_output_tokens = limits
    return ModelDescriptor(
        provider_id="openai",
        model_id=normalized,
        capabilities=frozenset(
            {
                ModelCapability.TEXT_GENERATION,
                ModelCapability.STRUCTURED_GENERATION,
            }
        ),
        context_window=context_window,
        max_output_tokens=max_output_tokens,
    )


def _build_model_gateway(
    settings: Settings,
    client: httpx.AsyncClient,
    model_id: str,
    credential: str,
) -> ModelGateway:
    return OpenAICloudGateway(
        client=client,
        api_key=credential,
        model=model_id,
        timeout_seconds=settings.model_timeout_seconds,
        readiness_timeout_seconds=settings.model_readiness_timeout_seconds,
    )


def openai_registration() -> ProviderRegistration:
    return ProviderRegistration(
        descriptor=ProviderDescriptor(
            provider_id="openai",
            display_name="OpenAI",
            routing_type=RoutingType.CLOUD,
        ),
        describe_model=_describe_model,
        credential_model_gateway_factory=_build_model_gateway,
    )
