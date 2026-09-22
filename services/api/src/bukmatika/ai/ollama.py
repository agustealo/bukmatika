import json
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
    StructuredResponseT,
)


class _OllamaMessage(BaseModel):
    content: str


class _OllamaChatResponse(BaseModel):
    message: _OllamaMessage


class _OllamaInstalledModel(BaseModel):
    name: str
    model: str | None = None


class _OllamaTagsResponse(BaseModel):
    models: list[_OllamaInstalledModel]


class OllamaLocalGateway(ModelGateway):
    """Loopback-only Ollama adapter using the native structured-output chat API."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        model: str,
        timeout_seconds: float,
        readiness_timeout_seconds: float,
    ) -> None:
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("Ollama model name cannot be blank")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("Ollama timeout must be between 0 and 60 seconds")
        if readiness_timeout_seconds <= 0 or readiness_timeout_seconds > 10:
            raise ValueError("Ollama readiness timeout must be between 0 and 10 seconds")
        self._client = client
        self._base_url = _validate_loopback_base_url(base_url)
        self._timeout_seconds = timeout_seconds
        self._readiness_timeout_seconds = readiness_timeout_seconds
        self._identity = ModelProviderIdentity(
            provider="ollama",
            model=normalized_model,
            routing="local",
        )

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        try:
            response = await self._client.get(
                f"{self._base_url}/api/tags",
                timeout=self._readiness_timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return self._readiness(ModelReadinessState.PROVIDER_UNREACHABLE, ready=False)

        try:
            tags = _OllamaTagsResponse.model_validate(response.json())
        except (ValueError, ValidationError):
            return self._readiness(ModelReadinessState.PROVIDER_INVALID, ready=False)

        installed = {
            candidate
            for item in tags.models
            for candidate in (item.name, item.model)
            if candidate is not None
        }
        if self._identity.model not in installed:
            return self._readiness(ModelReadinessState.MODEL_MISSING, ready=False)
        return self._readiness(ModelReadinessState.READY, ready=True)

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        body = {
            "model": self._identity.model,
            "stream": False,
            "think": False,
            "messages": [
                {"role": "system", "content": _system_prompt(request.task)},
                {
                    "role": "user",
                    "content": json.dumps(
                        request.payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "format": response_type.model_json_schema(),
            "options": {
                "temperature": 0,
                "num_predict": request.max_output_tokens,
            },
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/api/chat",
                json=body,
                timeout=min(request.timeout_seconds, self._timeout_seconds),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ModelProviderRequestFailed("Local Ollama request failed") from exc

        try:
            envelope = _OllamaChatResponse.model_validate(response.json())
            return response_type.model_validate_json(envelope.message.content)
        except (ValueError, ValidationError) as exc:
            raise ModelProviderResponseInvalid(
                "Local Ollama returned an invalid structured response"
            ) from exc

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


def _system_prompt(task: ModelTask) -> str:
    if task is ModelTask.RESEARCH_ANSWER:
        return (
            "Return only JSON matching the supplied schema. Use only the evidence included in the "
            "request. Every claim must cite one or more supplied evidence_id values. Do not invent "
            "evidence IDs, source coordinates, facts, or citations. If the evidence is "
            "insufficient, return a claim that states the limitation and cite the evidence that "
            "establishes the available context."
        )
    return (
        "Return only JSON matching the supplied schema. Follow the request exactly and do not "
        "invent product state, capabilities, identifiers, or policy decisions."
    )


def _validate_loopback_base_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlsplit(candidate)
    if parsed.scheme != "http":
        raise ValueError("Local Ollama URL must use http")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Local Ollama URL cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Local Ollama URL cannot contain query or fragment components")
    if parsed.path not in ("", "/"):
        raise ValueError("Local Ollama URL must be an origin without a path")
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if hostname == "localhost":
        return candidate
    try:
        address = ip_address(hostname)
    except ValueError as exc:
        raise ValueError("Local Ollama URL must use localhost or a loopback IP") from exc
    if not address.is_loopback:
        raise ValueError("Local Ollama URL must use a loopback IP")
    return candidate
