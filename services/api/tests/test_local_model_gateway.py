import json

import httpx
import pytest
from pydantic import BaseModel

from bukmatika.ai.factory import build_model_gateway
from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelRequest,
    ModelTask,
)
from bukmatika.ai.ollama import OllamaLocalGateway
from bukmatika.config import Settings


class _StructuredProbe(BaseModel):
    value: str


def _request(timeout_seconds: float = 30) -> ModelRequest:
    return ModelRequest(
        task=ModelTask.RESEARCH_ANSWER,
        payload={"question": "What does the source establish?", "evidence": []},
        data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
        max_output_tokens=128,
        timeout_seconds=timeout_seconds,
    )


async def test_ollama_gateway_uses_native_schema_output_and_local_identity() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps({"value": "grounded"})}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OllamaLocalGateway(
            client=client,
            base_url="http://127.0.0.1:11434",
            model="qwen3:8b",
            timeout_seconds=7,
        )
        result = await gateway.generate_structured(_request(timeout_seconds=30), _StructuredProbe)

    assert result == _StructuredProbe(value="grounded")
    assert gateway.identity.provider == "ollama"
    assert gateway.identity.model == "qwen3:8b"
    assert gateway.identity.routing == "local"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "qwen3:8b"
    assert body["stream"] is False
    assert body["think"] is False
    assert body["format"] == _StructuredProbe.model_json_schema()
    assert body["options"] == {"temperature": 0, "num_predict": 128}
    timeout = captured["timeout"]
    assert isinstance(timeout, dict)
    assert timeout["read"] == 7


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:11434",
        "http://10.0.0.20:11434",
        "http://example.com:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434/?token=secret",
    ],
)
def test_ollama_gateway_rejects_non_loopback_or_unsafe_origins(base_url: str) -> None:
    with pytest.raises(ValueError):
        OllamaLocalGateway(
            client=httpx.AsyncClient(),
            base_url=base_url,
            model="qwen3:8b",
            timeout_seconds=30,
        )


def test_model_factory_is_explicit_and_fail_closed_without_model() -> None:
    client = httpx.AsyncClient()
    disabled = build_model_gateway(
        settings=Settings(model_provider="none"),
        client=client,
    )
    missing_model = build_model_gateway(
        settings=Settings(model_provider="ollama", ollama_model=""),
        client=client,
    )
    assert disabled.identity is None
    assert missing_model.identity is None


async def test_ollama_gateway_rejects_invalid_structured_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OllamaLocalGateway(
            client=client,
            base_url="http://[::1]:11434",
            model="qwen3:8b",
            timeout_seconds=30,
        )
        with pytest.raises(ModelProviderResponseInvalid):
            await gateway.generate_structured(_request(), _StructuredProbe)


async def test_ollama_gateway_translates_transport_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OllamaLocalGateway(
            client=client,
            base_url="http://localhost:11434",
            model="qwen3:8b",
            timeout_seconds=30,
        )
        with pytest.raises(ModelProviderRequestFailed):
            await gateway.generate_structured(_request(), _StructuredProbe)
