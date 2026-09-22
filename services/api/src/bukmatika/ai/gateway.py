from enum import StrEnum
from typing import Protocol, TypeVar

from pydantic import BaseModel, Field, JsonValue


class ModelTask(StrEnum):
    PLAN = "plan"


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


StructuredResponseT = TypeVar("StructuredResponseT", bound=BaseModel)


class ModelGateway(Protocol):
    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT: ...


class UnconfiguredModelGateway:
    """Fail-closed gateway used until an explicit provider is configured."""

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        del request, response_type
        raise ModelProviderUnconfigured("No model provider is configured")
