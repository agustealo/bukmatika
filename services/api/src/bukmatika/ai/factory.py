import httpx

from bukmatika.ai.gateway import ModelGateway, UnconfiguredModelGateway
from bukmatika.ai.ollama import OllamaLocalGateway
from bukmatika.config import Settings


def build_model_gateway(
    *,
    settings: Settings,
    client: httpx.AsyncClient,
) -> ModelGateway:
    """Construct the one configured model gateway for this API process."""
    if settings.model_provider == "none":
        return UnconfiguredModelGateway()
    model = (settings.ollama_model or "").strip()
    if not model:
        return UnconfiguredModelGateway()
    return OllamaLocalGateway(
        client=client,
        base_url=settings.ollama_base_url,
        model=model,
        timeout_seconds=settings.model_timeout_seconds,
    )


def model_provider_configured(gateway: ModelGateway) -> bool:
    return gateway.identity is not None
