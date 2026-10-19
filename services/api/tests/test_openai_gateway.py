import json

import httpx
import pytest
from pydantic import BaseModel

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderAuthenticationFailed,
    ModelProviderQuotaExceeded,
    ModelProviderRateLimited,
    ModelProviderRefused,
    ModelProviderRequestFailed,
    ModelProviderResponseIncomplete,
    ModelProviderResponseInvalid,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
)
from bukmatika.ai.openai import OpenAICloudGateway


class _Answer(BaseModel):
    value: str


def _request() -> ModelRequest:
    return ModelRequest(
        task=ModelTask.RUNTIME_SMOKE,
        payload={"probe": "safe"},
        data_classification=ModelDataClassification.PUBLIC,
        max_output_tokens=64,
        timeout_seconds=3,
    )


def _gateway(client: httpx.AsyncClient, *, key: str = "sk-test-secret") -> OpenAICloudGateway:
    return OpenAICloudGateway(
        client=client,
        api_key=key,
        model="gpt-6-luna",
        timeout_seconds=10,
        readiness_timeout_seconds=2,
    )


async def test_readiness_uses_fixed_openai_model_endpoint_and_bearer_credential() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "gpt-6-luna"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        readiness = await _gateway(client).readiness()

    assert readiness.state is ModelReadinessState.READY
    assert readiness.ready is True
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.openai.com/v1/models/gpt-6-luna"
    assert seen[0].headers["authorization"] == "Bearer sk-test-secret"


async def test_structured_generation_uses_responses_api_without_secret_in_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {"type": "reasoning"},
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"value":"ok"}',
                                "annotations": [],
                            }
                        ],
                    },
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        result = await _gateway(client).generate_structured(_request(), _Answer)

    assert result.value == "ok"
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer sk-test-secret"
    payload = json.loads(request.content)
    assert payload["model"] == "gpt-6-luna"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 64
    assert payload["text"]["format"]["type"] == "json_schema"
    assert payload["text"]["format"]["strict"] is True
    assert payload["text"]["format"]["schema"]["properties"]["value"]["type"] == "string"
    assert json.loads(payload["input"]) == {"probe": "safe"}
    assert "sk-test-secret" not in request.content.decode("utf-8")


@pytest.mark.parametrize(
    ("status_code", "body", "error_type"),
    [
        (401, {"error": {"code": "invalid_api_key"}}, ModelProviderAuthenticationFailed),
        (429, {"error": {"code": "rate_limit_exceeded"}}, ModelProviderRateLimited),
        (429, {"error": {"code": "project_spend_limit_exceeded"}}, ModelProviderQuotaExceeded),
    ],
)
async def test_provider_failures_are_normalized_without_response_message_leakage(
    status_code: int,
    body: dict[str, object],
    error_type: type[Exception],
) -> None:
    provider_message = "provider diagnostic must not escape"
    error = body.get("error")
    assert isinstance(error, dict)
    error["message"] = provider_message

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json=body)),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(error_type) as caught:
            await _gateway(client).generate_structured(_request(), _Answer)

    assert provider_message not in str(caught.value)
    assert "sk-test-secret" not in str(caught.value)


async def test_redirect_is_not_followed_and_cannot_forward_credential() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://example.invalid/steal"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        trust_env=False,
    ) as client:
        with pytest.raises(ModelProviderRequestFailed):
            await _gateway(client).generate_structured(_request(), _Answer)

    assert requests == ["https://api.openai.com/v1/responses"]


@pytest.mark.parametrize(
    ("response_body", "error_type"),
    [
        (
            {"status": "incomplete", "output": []},
            ModelProviderResponseIncomplete,
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "refusal", "refusal": "cannot comply"}],
                    }
                ],
            },
            ModelProviderRefused,
        ),
        (
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "not-json"}],
                    }
                ],
            },
            ModelProviderResponseInvalid,
        ),
    ],
)
async def test_incomplete_refusal_and_invalid_structured_output_fail_closed(
    response_body: dict[str, object],
    error_type: type[Exception],
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response_body)),
        follow_redirects=False,
        trust_env=False,
    ) as client:
        with pytest.raises(error_type):
            await _gateway(client).generate_structured(_request(), _Answer)
