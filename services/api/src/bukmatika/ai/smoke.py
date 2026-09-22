import asyncio
import json
import sys
from typing import Literal

import httpx
from pydantic import BaseModel

from bukmatika.ai.factory import build_model_gateway
from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelGateway,
    ModelProviderNotReady,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelProviderUnconfigured,
    ModelRequest,
    ModelTask,
)
from bukmatika.config import get_settings

_SMOKE_TOKEN = "bukmatika-local-runtime-v1"


class RuntimeSmokeResponse(BaseModel):
    status: Literal["ready"]
    token: Literal["bukmatika-local-runtime-v1"]


class RuntimeSmokeResult(BaseModel):
    provider: str
    model: str
    routing: str
    status: Literal["ready"]
    verification_token: Literal["bukmatika-local-runtime-v1"]


async def run_model_runtime_smoke(
    gateway: ModelGateway,
    *,
    timeout_seconds: float,
) -> RuntimeSmokeResult:
    """Prove a configured real provider is ready and can satisfy structured output."""
    readiness = await gateway.readiness()
    if not readiness.configured:
        raise ModelProviderUnconfigured("No model provider is configured")
    if not readiness.ready:
        raise ModelProviderNotReady(readiness)
    identity = readiness.identity
    if identity is None:
        raise ModelProviderResponseInvalid("Ready model provider did not expose an identity")

    response = await gateway.generate_structured(
        ModelRequest(
            task=ModelTask.RUNTIME_SMOKE,
            payload={
                "status": "ready",
                "token": _SMOKE_TOKEN,
                "instruction": "Copy status and token exactly into the response schema.",
            },
            data_classification=ModelDataClassification.PUBLIC,
            max_output_tokens=64,
            timeout_seconds=timeout_seconds,
        ),
        RuntimeSmokeResponse,
    )
    return RuntimeSmokeResult(
        provider=identity.provider,
        model=identity.model,
        routing=identity.routing,
        status=response.status,
        verification_token=response.token,
    )


async def _run_from_settings() -> RuntimeSmokeResult:
    settings = get_settings()
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        gateway = build_model_gateway(settings=settings, client=client)
        return await run_model_runtime_smoke(
            gateway,
            timeout_seconds=settings.model_timeout_seconds,
        )


def _failure_payload(exc: Exception) -> dict[str, str]:
    if isinstance(exc, ModelProviderNotReady):
        return {
            "status": "failed",
            "code": exc.code,
            "state": exc.readiness.state.value,
        }
    return {
        "status": "failed",
        "code": str(getattr(exc, "code", type(exc).__name__)),
    }


def main() -> None:
    try:
        result = asyncio.run(_run_from_settings())
    except (
        ModelProviderUnconfigured,
        ModelProviderNotReady,
        ModelProviderRequestFailed,
        ModelProviderResponseInvalid,
    ) as exc:
        print(json.dumps(_failure_payload(exc), sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None

    print(result.model_dump_json())
