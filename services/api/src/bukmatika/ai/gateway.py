from enum import StrEnum
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field, JsonValue, model_validator


class ModelTask(StrEnum):
    PLAN = "plan"
    RESEARCH_ANSWER = "research_answer"
    RUNTIME_SMOKE = "runtime_smoke"


class ModelDataClassification(StrEnum):
    PUBLIC = "public"
    PRIVATE_USER_CONTEXT = "private_user_context"


class ModelProviderIdentity(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    routing: str = Field(min_length=1, max_length=32)


class ModelReadinessState(StrEnum):
    UNCONFIGURED = "unconfigured"
    PROVIDER_UNREACHABLE = "provider_unreachable"
    PROVIDER_INVALID = "provider_invalid"
    MODEL_MISSING = "model_missing"
    READY = "ready"


class ModelProviderReadiness(BaseModel):
    state: ModelReadinessState
    configured: bool
    ready: bool
    identity: ModelProviderIdentity | None = None


class ModelRequest(BaseModel):
    task: ModelTask
    payload: dict[str, JsonValue]
    data_classification: ModelDataClassification
    max_output_tokens: int = Field(ge=1, le=4_096)
    timeout_seconds: float = Field(gt=0, le=60)

    @model_validator(mode="after")
    def enforce_model_context_boundary(self) -> "ModelRequest":
        if self.task is not ModelTask.RESEARCH_ANSWER:
            return self
        if self.data_classification is not ModelDataClassification.PRIVATE_USER_CONTEXT:
            raise ValueError("research_answer requires private user context classification")

        question = self.payload.get("question")
        evidence = self.payload.get("evidence")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("research_answer requires a non-empty question")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("research_answer requires canonical evidence")

        minimized_evidence: list[JsonValue] = []
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError("research_answer evidence items must be objects")
            evidence_id = item.get("evidence_id")
            text = item.get("text")
            if not isinstance(evidence_id, str) or not evidence_id:
                raise ValueError("research_answer evidence requires an evidence_id")
            if not isinstance(text, str) or not text:
                raise ValueError("research_answer evidence requires text")
            minimized_evidence.append(
                {
                    "evidence_id": evidence_id,
                    "text": text,
                }
            )

        self.payload = {
            "question": question,
            "evidence": minimized_evidence,
        }
        return self


class ModelProviderUnconfigured(RuntimeError):
    code = "MODEL_PROVIDER_UNCONFIGURED"


class ModelProviderNotReady(RuntimeError):
    code = "MODEL_PROVIDER_NOT_READY"

    def __init__(self, readiness: ModelProviderReadiness) -> None:
        super().__init__(f"Model provider is not ready: {readiness.state.value}")
        self.readiness = readiness


class ModelProviderRequestFailed(RuntimeError):
    code = "MODEL_PROVIDER_REQUEST_FAILED"


class ModelProviderResponseInvalid(RuntimeError):
    code = "MODEL_PROVIDER_RESPONSE_INVALID"


StructuredResponseT = TypeVar("StructuredResponseT", bound=BaseModel)


class ModelGateway(Protocol):
    @property
    def identity(self) -> ModelProviderIdentity | None: ...

    async def readiness(self) -> ModelProviderReadiness: ...

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT: ...


class UnconfiguredModelGateway:
    """Fail-closed gateway used until an explicit provider is configured."""

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

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        del request, response_type
        raise ModelProviderUnconfigured("No model provider is configured")
