import math

import httpx
from pydantic import BaseModel, ValidationError

from bukmatika.ai.embedding_gateway import (
    EmbeddingBatch,
    EmbeddingGateway,
    EmbeddingProviderRequestFailed,
    EmbeddingProviderResponseInvalid,
    EmbeddingRequest,
)
from bukmatika.ai.gateway import (
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
)
from bukmatika.ai.ollama import (
    _model_aliases,
    _validate_loopback_base_url,
    inspect_ollama_models,
)


class _OllamaEmbedResponse(BaseModel):
    model: str
    embeddings: list[list[float]]


class OllamaLocalEmbeddingGateway(EmbeddingGateway):
    """Loopback-only Ollama adapter for bounded, request-local embeddings."""

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
            raise ValueError("Ollama embedding model name cannot be blank")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("Ollama embedding timeout must be between 0 and 60 seconds")
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
        inventory = await inspect_ollama_models(
            client=self._client,
            base_url=self._base_url,
            timeout_seconds=self._readiness_timeout_seconds,
        )
        if inventory.state is not ModelReadinessState.READY:
            return self._readiness(inventory.state, ready=False)
        requested_aliases = _model_aliases(self._identity.model)
        if set(inventory.models).isdisjoint(requested_aliases):
            return self._readiness(ModelReadinessState.MODEL_MISSING, ready=False)
        return self._readiness(ModelReadinessState.READY, ready=True)

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        body = {
            "model": self._identity.model,
            "input": request.inputs,
            "truncate": False,
        }
        try:
            response = await self._client.post(
                f"{self._base_url}/api/embed",
                json=body,
                timeout=min(request.timeout_seconds, self._timeout_seconds),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingProviderRequestFailed("Local Ollama embedding request failed") from exc

        try:
            envelope = _OllamaEmbedResponse.model_validate(response.json())
            if envelope.model not in _model_aliases(self._identity.model):
                raise ValueError("Ollama embedding response model does not match the request")
            if len(envelope.embeddings) != len(request.inputs):
                raise ValueError("Ollama returned the wrong number of embedding vectors")
            if any(not math.isfinite(value) for vector in envelope.embeddings for value in vector):
                raise ValueError("Ollama returned a non-finite embedding value")
            return EmbeddingBatch(
                identity=self._identity,
                vectors=envelope.embeddings,
            )
        except (ValueError, ValidationError) as exc:
            raise EmbeddingProviderResponseInvalid(
                "Local Ollama returned an invalid embedding response"
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
