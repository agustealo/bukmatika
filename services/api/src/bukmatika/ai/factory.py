from typing import Literal

import httpx

from bukmatika.ai.gateway import ModelGateway, UnconfiguredModelGateway
from bukmatika.ai.ollama import OllamaLocalGateway
from bukmatika.config import Settings

ModelProviderSelection = Literal["none", "ollama"]


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
    provider: ModelProviderSelection | str,
    model: str | None,
) -> ModelGateway:
    """Construct a gateway for a validated provider/model selection using installation routing."""
    if provider == "none":
        return UnconfiguredModelGateway()
    if provider != "ollama":
        raise ValueError(f"Unsupported model provider: {provider}")
    normalized_model = (model or "").strip()
    if not normalized_model:
        return UnconfiguredModelGateway()
    return OllamaLocalGateway(
        client=client,
        base_url=settings.ollama_base_url,
        model=normalized_model,
        timeout_seconds=settings.model_timeout_seconds,
        readiness_timeout_seconds=settings.model_readiness_timeout_seconds,
    )


def model_provider_configured(gateway: ModelGateway) -> bool:
    return gateway.identity is not None
