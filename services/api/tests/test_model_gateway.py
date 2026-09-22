import json

import httpx
import pytest
from pydantic import BaseModel

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderError,
    ModelRequest,
    ModelStructuredOutputError,
    ModelTask,
    OpenAIResponsesGateway,
    UnconfiguredModelGateway,
    build_default_model_gateway,
    model_gateway_identity,
)
from bukmatika.config import Settings


class _StructuredReply(BaseModel):
    value: str


def _request() -> ModelRequest:
    return ModelRequest(
        task=ModelTask.GROUNDED_RESEARCH,
        payload={"question": "What does the evidence say?"},
        data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
        max_output_tokens=256,
        timeout_seconds=5,
    )


async def test_openai_gateway_uses_responses_structured_output_without_storage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        payload = json.loads(request.content.decode("utf-8"))
        seen["payload"] = payload
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"value":"grounded"}',
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenAIResponsesGateway(
            api_key="test-secret",
            model="gpt-5.6-terra",
            client=client,
        )
        reply = await gateway.generate_structured(_request(), _StructuredReply)

    assert reply.value == "grounded"
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["authorization"] == "Bearer test-secret"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "gpt-5.6-terra"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 256
    text = payload["text"]
    assert isinstance(text, dict)
    output_format = text["format"]
    assert isinstance(output_format, dict)
    assert output_format["type"] == "json_schema"
    assert output_format["strict"] is False
    assert model_gateway_identity(gateway) == ("openai", "gpt-5.6-terra")


async def test_openai_gateway_rejects_invalid_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"wrong":"shape"}',
                            }
                        ],
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenAIResponsesGateway(
            api_key="test-secret",
            model="gpt-5.6-terra",
            client=client,
        )
        with pytest.raises(ModelStructuredOutputError):
            await gateway.generate_structured(_request(), _StructuredReply)


async def test_openai_gateway_normalizes_transport_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenAIResponsesGateway(
            api_key="test-secret",
            model="gpt-5.6-terra",
            client=client,
        )
        with pytest.raises(ModelProviderError, match="request failed"):
            await gateway.generate_structured(_request(), _StructuredReply)


def test_default_gateway_is_fail_closed_until_provider_and_key_are_configured() -> None:
    disabled = build_default_model_gateway(settings=Settings(ai_provider="none"))
    assert isinstance(disabled, UnconfiguredModelGateway)
    assert model_gateway_identity(disabled) == (None, None)

    missing_key = build_default_model_gateway(settings=Settings(ai_provider="openai"))
    assert isinstance(missing_key, UnconfiguredModelGateway)

    configured = build_default_model_gateway(
        settings=Settings(
            ai_provider="openai",
            openai_api_key="test-secret",
            openai_model="gpt-5.6-terra",
        )
    )
    assert isinstance(configured, OpenAIResponsesGateway)
    assert model_gateway_identity(configured) == ("openai", "gpt-5.6-terra")
