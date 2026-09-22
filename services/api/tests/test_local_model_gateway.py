import json

import httpx
import pytest
from pydantic import BaseModel

from bukmatika.ai.factory import build_model_gateway
from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
    UnconfiguredModelGateway,
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


def _gateway(
    client: httpx.AsyncClient,
    *,
    base_url: str = "http://127.0.0.1:11434",
    model: str = "qwen3:8b",
    timeout_seconds: float = 30,
    readiness_timeout_seconds: float = 2.5,
) -> OllamaLocalGateway:
    return OllamaLocalGateway(
        client=client,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        readiness_timeout_seconds=readiness_timeout_seconds,
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
        gateway = _gateway(client, timeout_seconds=7)
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


async def test_ollama_readiness_is_metadata_only_and_requires_requested_model() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "qwen3:8b", "model": "qwen3:8b"},
                    {"name": "gemma3:4b", "model": "gemma3:4b"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        readiness = await _gateway(
            client,
            readiness_timeout_seconds=1.25,
        ).readiness()

    assert readiness.state is ModelReadinessState.READY
    assert readiness.configured is True
    assert readiness.ready is True
    assert readiness.identity is not None
    assert readiness.identity.model == "qwen3:8b"
    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:11434/api/tags"
    assert captured["body"] == b""
    timeout = captured["timeout"]
    assert isinstance(timeout, dict)
    assert timeout["read"] == 1.25


async def test_ollama_readiness_accepts_implicit_latest_alias() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        return httpx.Response(
            200,
            json={"models": [{"name": "llama3.2:latest", "model": "llama3.2:latest"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        readiness = await _gateway(client, model="llama3.2").readiness()

    assert readiness.state is ModelReadinessState.READY
    assert readiness.ready is True


async def test_ollama_readiness_accepts_bare_name_for_explicit_latest_alias() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        return httpx.Response(
            200,
            json={"models": [{"name": "llama3.2", "model": "llama3.2"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        readiness = await _gateway(client, model="llama3.2:latest").readiness()

    assert readiness.state is ModelReadinessState.READY
    assert readiness.ready is True


async def test_ollama_readiness_does_not_accept_different_model_tag() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.content == b""
        return httpx.Response(
            200,
            json={"models": [{"name": "qwen3:4b", "model": "qwen3:4b"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        readiness = await _gateway(client, model="qwen3:8b").readiness()

    assert readiness.state is ModelReadinessState.MODEL_MISSING
    assert readiness.configured is True
    assert readiness.ready is False


async def test_ollama_readiness_distinguishes_unreachable_and_invalid_runtime() -> None:
    async def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as client:
        readiness = await _gateway(client).readiness()
    assert readiness.state is ModelReadinessState.PROVIDER_UNREACHABLE
    assert readiness.ready is False

    async def invalid(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"unexpected": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid)) as client:
        readiness = await _gateway(client).readiness()
    assert readiness.state is ModelReadinessState.PROVIDER_INVALID
    assert readiness.ready is False


async def test_unconfigured_gateway_readiness_is_local_and_network_free() -> None:
    readiness = await UnconfiguredModelGateway().readiness()
    assert readiness.state is ModelReadinessState.UNCONFIGURED
    assert readiness.configured is False
    assert readiness.ready is False
    assert readiness.identity is None


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
        _gateway(httpx.AsyncClient(), base_url=base_url)


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


async def test_model_factory_applies_separate_readiness_timeout() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = build_model_gateway(
            settings=Settings(
                model_provider="ollama",
                ollama_model="qwen3:8b",
                model_timeout_seconds=45,
                model_readiness_timeout_seconds=0.75,
            ),
            client=client,
        )
        readiness = await gateway.readiness()

    assert readiness.ready is True
    timeout = captured["timeout"]
    assert isinstance(timeout, dict)
    assert timeout["read"] == 0.75


async def test_ollama_gateway_rejects_invalid_structured_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = _gateway(client, base_url="http://[::1]:11434")
        with pytest.raises(ModelProviderResponseInvalid):
            await gateway.generate_structured(_request(), _StructuredProbe)


async def test_ollama_gateway_translates_transport_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = _gateway(client, base_url="http://localhost:11434")
        with pytest.raises(ModelProviderRequestFailed):
            await gateway.generate_structured(_request(), _StructuredProbe)
