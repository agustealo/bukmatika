from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from bukmatika.ai.delegation_domain import DelegationStatus
from bukmatika.research.domain import ResearchPassageResponse


class DelegatedResearchPassageReceipt(BaseModel):
    library_entry_id: UUID
    document_id: UUID
    chunk_id: UUID
    section_id: UUID
    section_ordinal: int = Field(ge=0)
    chunk_ordinal: int = Field(ge=0)
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    score: float


class DelegatedResearchSearchReceipt(BaseModel):
    version: Literal[1] = 1
    capability: Literal["research.search"] = "research.search"
    query: str
    selected_library_entry_ids: list[UUID]
    passages: list[DelegatedResearchPassageReceipt]


class DelegationResultSource(BaseModel):
    library_entry_id: UUID
    work_title: str | None = None
    edition_title: str | None = None
    available: bool


class DelegationResultBudget(BaseModel):
    selected_step_count: int = Field(ge=0)
    attempts_used: int = Field(ge=0)
    max_total_attempts: int = Field(ge=1)
    max_retries_per_step: int = Field(ge=0)
    max_runtime_seconds: int = Field(ge=30)
    started_at: datetime | None = None


class DelegationRecentResult(BaseModel):
    delegation_id: UUID
    attempt_id: UUID | None = None
    step_id: str | None = None
    capability: str | None = None
    status: DelegationStatus = DelegationStatus.COMPLETED
    outcome_at: datetime
    completed_at: datetime | None = None
    failure_code: str | None = None
    attempt_error_code: str | None = None
    user_request: str | None = None
    budget: DelegationResultBudget
    available: bool
    unavailable_reason: str | None = None
    query: str | None = None
    selected_library_entry_ids: list[UUID] = Field(default_factory=list)
    selected_sources: list[DelegationResultSource] = Field(default_factory=list)
    passages: list[ResearchPassageResponse] = Field(default_factory=list)
