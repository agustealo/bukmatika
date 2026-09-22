from typing import cast

import pytest
from pydantic import BaseModel

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelGateway,
    ModelProviderIdentity,
    ModelProviderNotReady,
    ModelProviderReadiness,
    ModelProviderUnconfigured,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
    UnconfiguredModelGateway,
)
from bukmatika.ai.smoke import RuntimeSmokeResponse, run_model_runtime_smoke


class _SmokeGateway:
    def __init__(self, readiness: ModelProviderReadiness) -> None:
        self._readiness = readiness
        self.request: ModelRequest | None = None

    @property
    def identity(self) -> ModelProviderIdentity | None:
        return self._readiness.identity

    async def readiness(self) -> ModelProviderReadiness:
        return self._readiness

    async def generate_structured(self, request, response_type):  # type: ignore[no-untyped-def]
        self.request = request
        return response_type.model_validate(
            {
                "status": "ready",
                "token": "bukmatika-local-runtime-v1",
            }
        )


def _ready_gateway() -> _SmokeGateway:
    return _SmokeGateway(
        ModelProviderReadiness(
            state=ModelReadinessState.READY,
            configured=True,
            ready=True,
            identity=ModelProviderIdentity(
                provider="ollama",
                model="qwen3:8b",
                routing="local",
            ),
        )
    )


async def test_runtime_smoke_requires_ready_provider_and_uses_only_public_probe_data() -> None:
    gateway = _ready_gateway()

    result = await run_model_runtime_smoke(
        cast(ModelGateway, gateway),
        timeout_seconds=12,
    )

    assert result.provider == "ollama"
    assert result.model == "qwen3:8b"
    assert result.routing == "local"
    assert result.status == "ready"
    assert result.verification_token == "bukmatika-local-runtime-v1"

    request = gateway.request
    assert request is not None
    assert request.task is ModelTask.RUNTIME_SMOKE
    assert request.data_classification is ModelDataClassification.PUBLIC
    assert request.max_output_tokens == 64
    assert request.timeout_seconds == 12
    assert request.payload == {
        "status": "ready",
        "token": "bukmatika-local-runtime-v1",
        "instruction": "Copy status and token exactly into the response schema.",
    }


async def test_runtime_smoke_fails_closed_when_provider_is_unconfigured() -> None:
    with pytest.raises(ModelProviderUnconfigured):
        await run_model_runtime_smoke(
            UnconfiguredModelGateway(),
            timeout_seconds=12,
        )


async def test_runtime_smoke_fails_closed_when_provider_is_not_ready() -> None:
    gateway = _SmokeGateway(
        ModelProviderReadiness(
            state=ModelReadinessState.MODEL_MISSING,
            configured=True,
            ready=False,
            identity=ModelProviderIdentity(
                provider="ollama",
                model="qwen3:8b",
                routing="local",
            ),
        )
    )

    with pytest.raises(ModelProviderNotReady) as captured:
        await run_model_runtime_smoke(
            cast(ModelGateway, gateway),
            timeout_seconds=12,
        )

    assert captured.value.readiness.state is ModelReadinessState.MODEL_MISSING
    assert gateway.request is None


def test_runtime_smoke_schema_requires_exact_verification_values() -> None:
    with pytest.raises(ValueError):
        RuntimeSmokeResponse.model_validate(
            {
                "status": "ready",
                "token": "wrong-token",
            }
        )

    assert issubclass(RuntimeSmokeResponse, BaseModel)
