import json
import re
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, ValidationError

from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderAuthenticationFailed,
    ModelProviderIdentity,
    ModelProviderQuotaExceeded,
    ModelProviderRateLimited,
    ModelProviderReadiness,
    ModelProviderRefused,
    ModelProviderRequestFailed,
    ModelProviderResponseIncomplete,
    ModelProviderResponseInvalid,
    ModelProviderUnavailable,
    ModelReadinessState,
    ModelRequest,
    StructuredResponseT,
)
from bukmatika.ai.model_prompts import system_prompt_for_task

OPENAI_API_ORIGIN = "https://api.openai.com"
_OPENAI_RESPONSES_URL = f"{OPENAI_API_ORIGIN}/v1/responses"
_OPENAI_MODELS_URL = f"{OPENAI_API_ORIGIN}/v1/models"
_QUOTA_CODES = {
    "credit_balance_exhausted",
    "insufficient_quota",
    "organization_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
    "project_spend_limit_exceeded",
}


class _OpenAIModel(BaseModel):
    id: str


class _OpenAIErrorDetail(BaseModel):
    code: str | None = None


class _OpenAIErrorEnvelope(BaseModel):
    error: _OpenAIErrorDetail


class _OpenAIContent(BaseModel):
    type: str
    text: str | None = None
    refusal: str | None = None


class _OpenAIOutputItem(BaseModel):
    type: str
    role: str | None = None
    content: list[_OpenAIContent] = Field(default_factory=list)


class _OpenAIResponse(BaseModel):
    status: str
    output: list[_OpenAIOutputItem]


class OpenAICloudGateway(ModelGateway):
    """Fixed-origin OpenAI Responses API adapter for structured generation."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        timeout_seconds: float,
        readiness_timeout_seconds: float,
    ) -> None:
        normalized_key = api_key.strip()
        normalized_model = model.strip()
        if not normalized_key:
            raise ValueError("OpenAI credential cannot be blank")
        if not normalized_model:
            raise ValueError("OpenAI model cannot be blank")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("OpenAI timeout must be between 0 and 60 seconds")
        if readiness_timeout_seconds <= 0 or readiness_timeout_seconds > 10:
            raise ValueError("OpenAI readiness timeout must be between 0 and 10 seconds")
        self._client = client
        self._api_key = normalized_key
        self._timeout_seconds = timeout_seconds
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._identity = ModelProviderIdentity(
            provider="openai",
            model=normalized_model,
            routing="cloud",
        )

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        try:
            response = await self._client.get(
                f"{_OPENAI_MODELS_URL}/{quote(self._identity.model, safe='')}",
                headers=self._headers(),
                timeout=self._readiness_timeout_seconds,
                follow_redirects=False,
            )
        except httpx.RequestError:
            return self._readiness(ModelReadinessState.PROVIDER_UNREACHABLE, ready=False)

        if response.status_code in (401, 403):
            return self._readiness(ModelReadinessState.PROVIDER_INVALID, ready=False)
        if response.status_code == 404:
            return self._readiness(ModelReadinessState.MODEL_MISSING, ready=False)
        if response.status_code == 429 or response.status_code >= 500:
            return self._readiness(ModelReadinessState.PROVIDER_UNREACHABLE, ready=False)
        if not response.is_success:
            return self._readiness(ModelReadinessState.PROVIDER_INVALID, ready=False)
        try:
            model = _OpenAIModel.model_validate(response.json())
        except (ValueError, ValidationError):
            return self._readiness(ModelReadinessState.PROVIDER_INVALID, ready=False)
        if model.id != self._identity.model:
            return self._readiness(ModelReadinessState.PROVIDER_INVALID, ready=False)
        return self._readiness(ModelReadinessState.READY, ready=True)

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        schema_name = _schema_name(response_type.__name__)
        body = {
            "model": self._identity.model,
            "store": False,
            "instructions": system_prompt_for_task(request.task),
            "input": json.dumps(
                request.payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "max_output_tokens": request.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": response_type.model_json_schema(),
                }
            },
        }
        try:
            response = await self._client.post(
                _OPENAI_RESPONSES_URL,
                headers=self._headers(),
                json=body,
                timeout=min(request.timeout_seconds, self._timeout_seconds),
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            raise ModelProviderUnavailable("OpenAI request could not reach the provider") from exc

        if not response.is_success:
            _raise_request_error(response)

        try:
            envelope = _OpenAIResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise ModelProviderResponseInvalid(
                "OpenAI returned an invalid response envelope"
            ) from exc

        if envelope.status == "incomplete":
            raise ModelProviderResponseIncomplete("OpenAI returned an incomplete response")
        if envelope.status != "completed":
            raise ModelProviderResponseInvalid("OpenAI returned a non-completed response")

        output_text: list[str] = []
        for item in envelope.output:
            if item.type != "message":
                continue
            for content in item.content:
                if content.type == "refusal":
                    raise ModelProviderRefused("OpenAI refused the structured generation request")
                if content.type == "output_text" and content.text is not None:
                    output_text.append(content.text)
        if not output_text:
            raise ModelProviderResponseInvalid(
                "OpenAI response contained no structured text output"
            )

        try:
            return response_type.model_validate_json("".join(output_text))
        except (ValueError, ValidationError) as exc:
            raise ModelProviderResponseInvalid(
                "OpenAI returned structured output that failed Bukmatika validation"
            ) from exc

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _readiness(
        self,
        state: ModelReadinessState,
        *,
        ready: bool,
    ) -> ModelProviderReadiness:
        return ModelProviderReadiness(
            state=state,
            configured=True,
            ready=ready,
            identity=self._identity,
        )


def _schema_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_-")
    if not normalized:
        normalized = "response"
    return f"bukmatika_{normalized}"[:64]


def _raise_request_error(response: httpx.Response) -> None:
    if response.status_code in (401, 403):
        raise ModelProviderAuthenticationFailed("OpenAI rejected the configured credential")
    if response.status_code == 429:
        code = _error_code(response)
        if code in _QUOTA_CODES:
            raise ModelProviderQuotaExceeded("OpenAI quota or spend limit is exhausted")
        raise ModelProviderRateLimited("OpenAI rate limit was reached")
    if response.status_code >= 500:
        raise ModelProviderUnavailable("OpenAI is temporarily unavailable")
    raise ModelProviderRequestFailed(
        f"OpenAI request failed with HTTP {response.status_code}"
    )


def _error_code(response: httpx.Response) -> str | None:
    try:
        return _OpenAIErrorEnvelope.model_validate(response.json()).error.code
    except (ValueError, ValidationError):
        return None
