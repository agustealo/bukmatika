from typing import Protocol

from pydantic import BaseModel, Field, field_validator, model_validator

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
)

MAX_EMBEDDING_INPUTS = 32
MAX_EMBEDDING_INPUT_CHARS = 12_000
MAX_EMBEDDING_REQUEST_CHARS = 131_072
MAX_EMBEDDING_DIMENSIONS = 8_192


class EmbeddingRequest(BaseModel):
    inputs: list[str] = Field(min_length=1, max_length=MAX_EMBEDDING_INPUTS)
    data_classification: ModelDataClassification
    timeout_seconds: float = Field(gt=0, le=60)

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        total_chars = 0
        for item in value:
            text = item.strip()
            if not text:
                raise ValueError("Embedding inputs cannot be blank")
            if len(text) > MAX_EMBEDDING_INPUT_CHARS:
                raise ValueError("Embedding input exceeds the per-item character budget")
            total_chars += len(text)
            normalized.append(text)
        if total_chars > MAX_EMBEDDING_REQUEST_CHARS:
            raise ValueError("Embedding request exceeds the total character budget")
        return normalized


class EmbeddingBatch(BaseModel):
    identity: ModelProviderIdentity
    vectors: list[list[float]] = Field(min_length=1, max_length=MAX_EMBEDDING_INPUTS)

    @model_validator(mode="after")
    def validate_vectors(self) -> "EmbeddingBatch":
        dimensions = len(self.vectors[0])
        if dimensions < 1 or dimensions > MAX_EMBEDDING_DIMENSIONS:
            raise ValueError("Embedding dimension count is outside the supported range")
        if any(len(vector) != dimensions for vector in self.vectors):
            raise ValueError("Embedding vectors must have consistent dimensions")
        return self


class EmbeddingProviderUnconfigured(RuntimeError):
    code = "EMBEDDING_PROVIDER_UNCONFIGURED"


class EmbeddingProviderNotReady(RuntimeError):
    code = "EMBEDDING_PROVIDER_NOT_READY"

    def __init__(self, readiness: ModelProviderReadiness) -> None:
        super().__init__(f"Embedding provider is not ready: {readiness.state.value}")
        self.readiness = readiness


class EmbeddingProviderRequestFailed(RuntimeError):
    code = "EMBEDDING_PROVIDER_REQUEST_FAILED"


class EmbeddingProviderResponseInvalid(RuntimeError):
    code = "EMBEDDING_PROVIDER_RESPONSE_INVALID"


class EmbeddingGateway(Protocol):
    @property
    def identity(self) -> ModelProviderIdentity | None: ...

    async def readiness(self) -> ModelProviderReadiness: ...

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch: ...


class UnconfiguredEmbeddingGateway:
    """Fail-closed embedding gateway used until an explicit local model is configured."""

    @property
    def identity(self) -> ModelProviderIdentity | None:
        return None

    async def readiness(self) -> ModelProviderReadiness:
        return ModelProviderReadiness(
            state=ModelReadinessState.UNCONFIGURED,
            configured=False,
            ready=False,
            identity=None,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        del request
        raise EmbeddingProviderUnconfigured("No embedding provider is configured")
