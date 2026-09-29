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
        if self.task not in (ModelTask.PLAN, ModelTask.RESEARCH_ANSWER):
            return self
        if self.data_classification is not ModelDataClassification.PRIVATE_USER_CONTEXT:
            raise ValueError(f"{self.task.value} requires private user context classification")
        if self.task is ModelTask.PLAN:
            self.payload = _minimize_plan_payload(self.payload)
        else:
            self.payload = _minimize_research_answer_payload(self.payload)
        return self


def _minimize_research_answer_payload(
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    question = payload.get("question")
    evidence = payload.get("evidence")
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

    return {
        "question": question,
        "evidence": minimized_evidence,
    }


def _minimize_plan_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    user_request = payload.get("user_request")
    context = payload.get("context")
    if not isinstance(user_request, str) or not user_request.strip():
        raise ValueError("plan requires a non-empty user request")
    if not isinstance(context, dict):
        raise ValueError("plan requires an explicit context object")

    task = context.get("task")
    preferences = context.get("preferences")
    goal = context.get("goal")
    library_entries = context.get("library_entries")
    available_capabilities = context.get("available_capabilities")
    if not isinstance(task, str) or not task:
        raise ValueError("plan context requires a task")
    if not isinstance(preferences, list):
        raise ValueError("plan context requires preferences")
    if goal is not None and not isinstance(goal, dict):
        raise ValueError("plan context goal must be an object or null")
    if not isinstance(library_entries, list):
        raise ValueError("plan context requires library entries")
    if not isinstance(available_capabilities, list) or not all(
        isinstance(capability, str) and capability for capability in available_capabilities
    ):
        raise ValueError("plan context requires available capabilities")

    minimized_preferences: list[JsonValue] = []
    for preference in preferences:
        if not isinstance(preference, dict):
            raise ValueError("plan preferences must be objects")
        key = preference.get("key")
        value = preference.get("value")
        scope_type = preference.get("scope_type")
        scope_value = preference.get("scope_value")
        if not isinstance(key, str) or not key:
            raise ValueError("plan preference requires a key")
        if not isinstance(value, dict):
            raise ValueError("plan preference requires a value object")
        if not isinstance(scope_type, str) or not scope_type:
            raise ValueError("plan preference requires a scope type")
        if not isinstance(scope_value, str):
            raise ValueError("plan preference requires a scope value")
        minimized_preferences.append(
            {
                "key": key,
                "value": value,
                "scope_type": scope_type,
                "scope_value": scope_value,
            }
        )

    minimized_goal: JsonValue = None
    if isinstance(goal, dict):
        title = goal.get("title")
        kind = goal.get("kind")
        scope = goal.get("scope")
        constraints = goal.get("constraints")
        if not isinstance(title, str) or not title:
            raise ValueError("plan goal requires a title")
        if not isinstance(kind, str) or not kind:
            raise ValueError("plan goal requires a kind")
        if not isinstance(scope, dict) or not isinstance(constraints, dict):
            raise ValueError("plan goal scope and constraints must be objects")
        minimized_goal = {
            "title": title,
            "kind": kind,
            "scope": scope,
            "constraints": constraints,
        }

    minimized_library_entries: list[JsonValue] = []
    for entry in library_entries:
        if not isinstance(entry, dict):
            raise ValueError("plan library entries must be objects")
        library_entry_id = entry.get("library_entry_id")
        title = entry.get("title")
        document_ids = entry.get("document_ids")
        if not isinstance(library_entry_id, str) or not library_entry_id:
            raise ValueError("plan library entry requires an id")
        if not isinstance(title, str) or not title:
            raise ValueError("plan library entry requires a title")
        if not isinstance(document_ids, list) or not all(
            isinstance(document_id, str) and document_id for document_id in document_ids
        ):
            raise ValueError("plan library entry requires document ids")
        minimized_library_entries.append(
            {
                "library_entry_id": library_entry_id,
                "title": title,
                "document_ids": document_ids,
            }
        )

    return {
        "user_request": user_request,
        "context": {
            "task": task,
            "preferences": minimized_preferences,
            "goal": minimized_goal,
            "library_entries": minimized_library_entries,
            "available_capabilities": available_capabilities,
        },
    }


class ModelProviderUnconfigured(RuntimeError):
    code = "MODEL_PROVIDER_UNCONFIGURED"


class ModelProviderNotReady(RuntimeError):
    code = "MODEL_PROVIDER_NOT_READY"

    def __init__(self, readiness: ModelProviderReadiness) -> None:
        super().__init__(f"Model provider is not ready: {readiness.state.value}")
        self.readiness = readiness


class ModelProviderRequestFailed(RuntimeError):
    code = "MODEL_PROVIDER_REQUEST_FAILED"


class ModelProviderAuthenticationFailed(ModelProviderRequestFailed):
    code = "MODEL_PROVIDER_AUTHENTICATION_FAILED"


class ModelProviderRateLimited(ModelProviderRequestFailed):
    code = "MODEL_PROVIDER_RATE_LIMITED"


class ModelProviderQuotaExceeded(ModelProviderRequestFailed):
    code = "MODEL_PROVIDER_QUOTA_EXCEEDED"


class ModelProviderUnavailable(ModelProviderRequestFailed):
    code = "MODEL_PROVIDER_UNAVAILABLE"


class ModelProviderResponseInvalid(RuntimeError):
    code = "MODEL_PROVIDER_RESPONSE_INVALID"


class ModelProviderResponseIncomplete(ModelProviderResponseInvalid):
    code = "MODEL_PROVIDER_RESPONSE_INCOMPLETE"


class ModelProviderRefused(ModelProviderResponseInvalid):
    code = "MODEL_PROVIDER_REFUSED"


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
