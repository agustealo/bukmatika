from typing import Protocol

from bukmatika.ai.domain import ModelRequest, ModelResponse


class ModelGatewayError(RuntimeError):
    code = "MODEL_GATEWAY_ERROR"


class ModelProviderUnconfigured(ModelGatewayError):
    code = "MODEL_PROVIDER_UNCONFIGURED"


class ModelGateway(Protocol):
    """Only boundary through which Bukmatika may access a model provider."""

    async def generate_structured(self, request: ModelRequest) -> ModelResponse: ...


class UnconfiguredModelGateway:
    """Real fail-closed gateway state when no model provider is configured."""

    async def generate_structured(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelProviderUnconfigured("No model provider is configured")


def model_gateway() -> ModelGateway:
    return UnconfiguredModelGateway()
