from __future__ import annotations

import json
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, TypeVar, runtime_checkable

import httpx
from pydantic import BaseModel, Field, JsonValue, ValidationError

if TYPE_CHECKING:
    from bukmatika.config import Settings

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


class ModelTask(StrEnum):
    PLAN = "plan"
    GROUNDED_RESEARCH = "grounded_research"


class ModelDataClassification(StrEnum):
    PUBLIC = "public"
    PRIVATE_USER_CONTEXT = "private_user_context"


class ModelRequest(BaseModel):
    task: ModelTask
    payload: dict[str, JsonValue]
    data_classification: ModelDataClassification
    max_output_tokens: int = Field(ge=1, le=4_096)
    timeout_seconds: float = Field(gt=0, le=60)


class ModelProviderUnconfigured(RuntimeError):
    code = "MODEL_PROVIDER_UNCONFIGURED"


class ModelProviderError(RuntimeError):
    code = "MODEL_PROVIDER_ERROR"


class ModelStructuredOutputError(RuntimeError):
    code = "MODEL_STRUCTURED_OUTPUT_INVALID"


StructuredResponseT = TypeVar("StructuredResponseT", bound=BaseModel)


class ModelGateway(Protocol):
    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT: ...


@runtime_checkable
class IdentifiedModelGateway(Protocol):
    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...


class UnconfiguredModelGateway:
    """Fail-closed gateway used until an explicit provider is configured."""

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        del request, response_type
        raise ModelProviderUnconfigured("No model provider is configured")


class OpenAIResponsesGateway:
    """Concrete structured-output provider behind Bukmatika's canonical gateway."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_key = api_key.strip()
        normalized_model = model.strip()
        if not normalized_key:
            raise ValueError("OpenAI API key cannot be blank")
        if not normalized_model:
            raise ValueError("OpenAI model cannot be blank")
        self._api_key = normalized_key
        self._model = normalized_model
        self._client = client

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model_name(self) -> str:
        return self._model

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        payload: dict[str, object] = {
            "model": self._model,
            "store": False,
            "max_output_tokens": request.max_output_tokens,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _task_instructions(request.task),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": json.dumps(
                                request.payload,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": _schema_name(response_type),
                    "schema": response_type.model_json_schema(),
                    "strict": False,
                }
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            if self._client is not None:
                response = await self._client.post(
                    OPENAI_RESPONSES_URL,
                    json=payload,
                    headers=headers,
                    timeout=request.timeout_seconds,
                )
            else:
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    trust_env=False,
                    timeout=request.timeout_seconds,
                ) as client:
                    response = await client.post(
                        OPENAI_RESPONSES_URL,
                        json=payload,
                        headers=headers,
                    )
        except httpx.RequestError as exc:
            raise ModelProviderError("OpenAI Responses API request failed") from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise ModelProviderError(
                f"OpenAI Responses API returned HTTP {response.status_code}"
            )

        try:
            provider_payload: object = response.json()
        except ValueError as exc:
            raise ModelProviderError("OpenAI Responses API returned invalid JSON") from exc

        output_text = _extract_output_text(provider_payload)
        try:
            return response_type.model_validate_json(output_text)
        except ValidationError as exc:
            raise ModelStructuredOutputError(
                "Model response did not satisfy the requested structured-output contract"
            ) from exc


def build_default_model_gateway(
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> ModelGateway:
    if settings is None:
        from bukmatika.config import get_settings

        settings = get_settings()

    if settings.ai_provider != "openai" or settings.openai_api_key is None:
        return UnconfiguredModelGateway()

    return OpenAIResponsesGateway(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        client=client,
    )


def model_gateway_identity(gateway: ModelGateway) -> tuple[str | None, str | None]:
    if isinstance(gateway, IdentifiedModelGateway):
        return gateway.provider_name, gateway.model_name
    return None, None


def _task_instructions(task: ModelTask) -> str:
    if task is ModelTask.GROUNDED_RESEARCH:
        return (
            "Answer only from the supplied evidence packets. Return the requested JSON shape. "
            "Every substantive claim must include at least one supplied evidence_id. Never invent, "
            "alter or cite an evidence_id that is not present in the request. "
            "If the evidence does not support a claim, omit that claim."
        )
    return (
        "Return only a bounded structured plan that satisfies the requested JSON schema. "
        "Use only capabilities and context present in the request; do not invent capabilities."
    )


def _schema_name(response_type: type[BaseModel]) -> str:
    value = "".join(
        character
        for character in response_type.__name__
        if character.isalnum() or character in "_-"
    )
    return (value or "bukmatika_response")[:64]


def _extract_output_text(payload: object) -> str:
    if not isinstance(payload, dict):
        raise ModelProviderError("OpenAI Responses API returned an unexpected payload")
    output = payload.get("output")
    if not isinstance(output, list):
        raise ModelProviderError("OpenAI Responses API returned no output items")

    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise ModelProviderError("Model provider refused the structured request")
            if part.get("type") != "output_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                return text

    raise ModelProviderError("OpenAI Responses API returned no structured output text")
