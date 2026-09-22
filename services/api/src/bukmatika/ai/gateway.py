from enum import StrEnum
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field, JsonValue


class ModelTask(StrEnum):
    PLAN = "plan"
    RESEARCH_ANSWER = "research_answer"


class ModelDataClassification(StrEnum):
    PUBLIC = "public"
    PRIVATE_USER_CONTEXT = "private_user_context"


class ModelProviderIdentity(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=255)
    routing: str = Field(min_length=1, max_length=32)


class ModelRequest(BaseModel):
    task: ModelTask
    payload: dict[str, JsonValue]
    data_classification: ModelDataClassification
    max_output_tokens: int = Field(ge=1, le=4_096)
    timeout_seconds: float = Field(gt=0, le=60)


class ModelProviderUnconfigured(RuntimeError):
    code = "MODEL_PROVIDER_UNCONFIGURED"


class ModelProviderRequestFailed(RuntimeError):
    code = "MODEL_PROVIDER_REQUEST_FAILED"


class ModelProviderResponseInvalid(RuntimeError):
    code = "MODEL_PROVIDER_RESPONSE_INVALID"


StructuredResponseT = TypeVar("StructuredResponseT", bound=BaseModel)


class ModelGateway(Protocol):
    @property
    def identity(self) -> ModelProviderIdentity | None: ...

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

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        del request, response_type
        raise ModelProviderUnconfigured("No model provider is configured")
