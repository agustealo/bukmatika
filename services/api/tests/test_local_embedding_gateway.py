import json

import httpx
import pytest

from bukmatika.ai.embedding_gateway import (
    EmbeddingProviderRequestFailed,
    EmbeddingProviderResponseInvalid,
    EmbeddingProviderUnconfigured,
    EmbeddingRequest,
    UnconfiguredEmbeddingGateway,
)
from bukmatika.ai.factory import build_embedding_gateway
from bukmatika.ai.gateway import ModelDataClassification, ModelReadinessState
from bukmatika.ai.ollama_embedding import OllamaLocalEmbeddingGateway
from bukmatika.config import Settings


def _request(timeout_seconds: float = 30) -> EmbeddingRequest:
    return EmbeddingRequest(
        inputs=["query text", "canonical passage"],
        data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
        timeout_seconds=timeout_seconds,
    )


def _gateway(
    client: httpx.AsyncClient,
    *,
    base_url: str = "http://127.0.0.1:11434",
    model: str = "embeddinggemma:latest",
    timeout_seconds: float = 30,
    readiness_timeout_seconds: float = 2.5,
) -> OllamaLocalEmbeddingGateway:
    return OllamaLocalEmbeddingGateway(
        client=client,
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        readiness_timeout_seconds=readiness_timeout_seconds,
    )


async def test_ollama_embedding_gateway_uses_current_batch_embed_contract() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(
            200,
            json={
                "model": "embeddinggemma:latest",
                "embeddings": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = _gateway(client, timeout_seconds=7)
        batch = await gateway.embed(_request(timeout_seconds=30))

    assert gateway.identity.provider == "ollama"
    assert gateway.identity.model == "embeddinggemma:latest"
    assert gateway.identity.routing == "local"
    assert batch.identity == gateway.identity
    assert batch.vectors == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert captured["url"] == "http://127.0.0.1:11434/api/embed"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body == {
        "model": "embeddinggemma:latest",
        "input": ["query text", "canonical passage"],
        "truncate": False,
    }
    timeout = captured["timeout"]
    assert isinstance(timeout, dict)
    assert timeout["read"] == 7


async def test_ollama_embedding_readiness_is_metadata_only() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={"models": [{"name": "embeddinggemma:latest"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        readiness = await _gateway(client).readiness()

    assert readiness.state is ModelReadinessState.READY
    assert readiness.ready is True
    assert readiness.identity is not None
    assert readiness.identity.model == "embeddinggemma:latest"
    assert captured["method"] == "GET"
    assert captured["url"] == "http://127.0.0.1:11434/api/tags"
    assert captured["body"] == b""


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "other-model", "embeddings": [[1.0], [1.0]]},
        {"model": "embeddinggemma:latest", "embeddings": [[1.0]]},
        {"model": "embeddinggemma:latest", "embeddings": [[1.0], [1.0, 0.0]]},
        {"model": "embeddinggemma:latest", "embeddings": [[float("nan")], [1.0]]},
    ],
)
async def test_ollama_embedding_gateway_rejects_invalid_responses(
    payload: dict[str, object],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingProviderResponseInvalid):
            await _gateway(client).embed(_request())


async def test_ollama_embedding_gateway_translates_transport_failure() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(EmbeddingProviderRequestFailed):
            await _gateway(client).embed(_request())


async def test_unconfigured_embedding_gateway_is_fail_closed() -> None:
    gateway = UnconfiguredEmbeddingGateway()
    readiness = await gateway.readiness()

    assert readiness.state is ModelReadinessState.UNCONFIGURED
    assert readiness.configured is False
    assert readiness.ready is False
    assert readiness.identity is None
    with pytest.raises(EmbeddingProviderUnconfigured):
        await gateway.embed(_request())


def test_embedding_factory_requires_local_provider_and_dedicated_model() -> None:
    client = httpx.AsyncClient()
    disabled = build_embedding_gateway(
        settings=Settings(model_provider="none", ollama_embedding_model="embeddinggemma"),
        client=client,
    )
    missing_model = build_embedding_gateway(
        settings=Settings(model_provider="ollama", ollama_embedding_model=""),
        client=client,
    )

    assert disabled.identity is None
    assert missing_model.identity is None
