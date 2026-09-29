from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from bukmatika.ai.provider_registry import ModelCapability, RoutingType


class ProviderConnectionStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


class ModelRole(StrEnum):
    PRIMARY = "primary"
    RESEARCH = "research"
    FAST = "fast"
    REASONING = "reasoning"
    EMBEDDINGS = "embeddings"
    FALLBACK = "fallback"


class ProviderDescriptorResponse(BaseModel):
    provider_id: str
    display_name: str
    routing_type: RoutingType


class ProviderCatalogResponse(BaseModel):
    providers: list[ProviderDescriptorResponse]


class ProviderConnectionCreate(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)


class ProviderConnectionUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    enabled: bool


class ProviderCredentialUpdate(BaseModel):
    secret: str = Field(min_length=1, max_length=16_384)


class ModelAssignmentUpdate(BaseModel):
    connection_id: UUID
    model_id: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=0, ge=0, le=100)


class ModelAssignmentResponse(BaseModel):
    assignment_id: UUID
    connection_id: UUID
    role: ModelRole
    model_id: str
    priority: int
    enabled: bool
    capabilities: list[ModelCapability]


class ModelAssignmentListResponse(BaseModel):
    assignments: list[ModelAssignmentResponse]


class ProviderConnectionResponse(BaseModel):
    connection_id: UUID
    provider_id: str
    display_name: str | None
    routing_type: RoutingType
    status: ProviderConnectionStatus
    credential_configured: bool
    created_at: datetime
    updated_at: datetime


class ProviderConnectionListResponse(BaseModel):
    connections: list[ProviderConnectionResponse]
