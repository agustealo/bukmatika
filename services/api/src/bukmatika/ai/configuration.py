from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.factory import (
    build_model_gateway,
    build_provider_registry,
    build_selected_model_gateway,
)
from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
    ModelRequest,
    StructuredResponseT,
    UnconfiguredModelGateway,
)
from bukmatika.ai.ollama import inspect_ollama_models
from bukmatika.ai.provider_registry import ProviderRegistry
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.providers import (
    ProviderAssignmentNotFound,
    ProviderConnectionRepository,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
PRIMARY_GENERATION_ROLE = "primary"


class ModelConfigurationMode(StrEnum):
    INSTALLATION_DEFAULT = "installation_default"
    DISABLED = "disabled"
    OLLAMA = "ollama"


class ModelConfigurationSource(StrEnum):
    INSTALLATION = "installation"
    PROFILE = "profile"


class LocalModelConfigurationUpdate(BaseModel):
    mode: ModelConfigurationMode
    model: str | None = Field(default=None, max_length=255)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    @model_validator(mode="after")
    def validate_model_selection(self) -> "LocalModelConfigurationUpdate":
        if self.mode is ModelConfigurationMode.OLLAMA:
            if not self.model:
                raise ValueError("An Ollama model is required")
            if not _valid_model_name(self.model):
                raise ValueError("Ollama model name contains unsupported characters")
        elif self.model is not None:
            raise ValueError("Model is only valid when mode is ollama")
        return self


class LocalModelConfigurationResponse(BaseModel):
    mode: ModelConfigurationMode
    source: ModelConfigurationSource
    selected_model: str | None
    effective_provider: str | None
    effective_model: str | None
    installation_provider: str | None
    installation_model: str | None
    routing: Literal["local"] = "local"


class LocalModelInventoryResponse(BaseModel):
    state: ModelReadinessState
    models: list[str]
    routing: Literal["local"] = "local"


@dataclass(frozen=True, slots=True)
class PrincipalModelRuntime:
    gateway: ModelGateway
    configuration: LocalModelConfigurationResponse


class _EphemeralSelectedGateway:
    """Principal-specific gateway with no mutable process-global provider selection."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider: str,
        model: str | None,
        registry: ProviderRegistry,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._model = model
        self._registry = registry
        normalized_model = (model or "").strip()
        if provider == "none" or not normalized_model:
            self._identity = None
        else:
            descriptor = registry.descriptor(provider)
            self._identity = ModelProviderIdentity(
                provider=provider,
                model=normalized_model,
                routing=descriptor.routing_type.value,
            )

    @property
    def identity(self) -> ModelProviderIdentity | None:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            gateway = build_selected_model_gateway(
                settings=self._settings,
                client=client,
                provider=self._provider,
                model=self._model,
                registry=self._registry,
            )
            return await gateway.readiness()

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            gateway = build_selected_model_gateway(
                settings=self._settings,
                client=client,
                provider=self._provider,
                model=self._model,
                registry=self._registry,
            )
            return await gateway.generate_structured(request, response_type)


class PrincipalModelRuntimeResolver:
    """Resolve one principal's effective gateway without changing installation network routing."""

    def __init__(
        self,
        *,
        settings: Settings,
        installation_gateway: ModelGateway | None = None,
        client: httpx.AsyncClient | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._installation_gateway = installation_gateway
        self._client = client
        self._session_scope = session_scope_factory
        self._provider_registry = provider_registry or build_provider_registry()

    async def resolve(self, *, principal_id: UUID) -> PrincipalModelRuntime:
        async with self._session_scope() as database_session:
            user_model = await PersonalizationRepository(database_session).get_or_create_user_model(
                principal_id
            )
            provider_override = user_model.model_provider_override
            model_override = user_model.model_name_override
            selected_provider = provider_override
            selected_model = model_override

            if provider_override not in (None, "none"):
                provider_repository = ProviderConnectionRepository(database_session)
                try:
                    assignment = await provider_repository.primary_assignment(
                        principal_id=principal_id,
                        role=PRIMARY_GENERATION_ROLE,
                    )
                except ProviderAssignmentNotFound:
                    # Compatibility bridge for a pre-backfill profile. New writes always create
                    # provider-neutral assignment state before returning to the caller.
                    pass
                else:
                    connection = await provider_repository.get_connection(
                        principal_id=principal_id,
                        connection_id=assignment.connection_id,
                        enabled_only=True,
                    )
                    selected_provider = connection.provider_id
                    selected_model = assignment.model_id

        configuration = self._configuration_response(
            provider_override=provider_override,
            selected_provider=selected_provider,
            selected_model=selected_model,
        )
        if provider_override is None:
            if self._installation_gateway is not None:
                gateway = self._installation_gateway
            elif self._client is not None:
                gateway = build_model_gateway(settings=self._settings, client=self._client)
            else:
                gateway = _EphemeralSelectedGateway(
                    settings=self._settings,
                    provider=self._settings.model_provider,
                    model=self._settings.ollama_model,
                    registry=self._provider_registry,
                )
        elif provider_override == "none":
            gateway = UnconfiguredModelGateway()
        elif self._client is not None:
            gateway = build_selected_model_gateway(
                settings=self._settings,
                client=self._client,
                provider=selected_provider or "none",
                model=selected_model,
                registry=self._provider_registry,
            )
        else:
            gateway = _EphemeralSelectedGateway(
                settings=self._settings,
                provider=selected_provider or "none",
                model=selected_model,
                registry=self._provider_registry,
            )
        return PrincipalModelRuntime(gateway=gateway, configuration=configuration)

    async def configuration(self, *, principal_id: UUID) -> LocalModelConfigurationResponse:
        return (await self.resolve(principal_id=principal_id)).configuration

    async def update_configuration(
        self,
        *,
        principal_id: UUID,
        update: LocalModelConfigurationUpdate,
    ) -> LocalModelConfigurationResponse:
        provider_override: str | None
        model_override: str | None
        if update.mode is ModelConfigurationMode.INSTALLATION_DEFAULT:
            provider_override = None
            model_override = None
        elif update.mode is ModelConfigurationMode.DISABLED:
            provider_override = "none"
            model_override = None
        else:
            provider_override = "ollama"
            model_override = update.model

        async with self._session_scope() as database_session:
            personalization_repository = PersonalizationRepository(database_session)
            user_model = await personalization_repository.update_model_configuration(
                principal_id=principal_id,
                provider_override=provider_override,
                model_name_override=model_override,
            )
            provider_repository = ProviderConnectionRepository(database_session)
            if provider_override == "ollama" and model_override is not None:
                connections = await provider_repository.list_connections(principal_id=principal_id)
                connection = next(
                    (
                        item
                        for item in connections
                        if item.provider_id == "ollama" and item.status == "enabled"
                    ),
                    None,
                )
                if connection is None:
                    connection = await provider_repository.create_connection(
                        principal_id=principal_id,
                        provider_id="ollama",
                        routing_type="local",
                        display_name="Ollama",
                    )
                await provider_repository.assign_model(
                    principal_id=principal_id,
                    connection_id=connection.id,
                    role=PRIMARY_GENERATION_ROLE,
                    model_id=model_override,
                )
            else:
                await provider_repository.disable_role_assignments(
                    principal_id=principal_id,
                    role=PRIMARY_GENERATION_ROLE,
                )

            await InteractionEventRepository(database_session).record(
                SemanticEventType.MODEL_CONFIGURATION_UPDATED,
                principal_id=principal_id,
                entity_type="user_model",
                entity_id=user_model.id,
                context={
                    "mode": update.mode.value,
                    "provider": provider_override,
                    "model": model_override,
                },
            )
        return self._configuration_response(
            provider_override=provider_override,
            selected_provider=provider_override,
            selected_model=model_override,
        )

    async def installed_models(self) -> LocalModelInventoryResponse:
        """Transitional Ollama inventory surface; not part of canonical provider routing."""
        if self._client is not None:
            inventory = await inspect_ollama_models(
                client=self._client,
                base_url=self._settings.ollama_base_url,
                timeout_seconds=self._settings.model_readiness_timeout_seconds,
            )
        else:
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                inventory = await inspect_ollama_models(
                    client=client,
                    base_url=self._settings.ollama_base_url,
                    timeout_seconds=self._settings.model_readiness_timeout_seconds,
                )
        return LocalModelInventoryResponse(
            state=inventory.state,
            models=inventory.models,
        )

    def _configuration_response(
        self,
        *,
        provider_override: str | None,
        selected_provider: str | None,
        selected_model: str | None,
    ) -> LocalModelConfigurationResponse:
        installation_provider = (
            self._settings.model_provider if self._settings.model_provider != "none" else None
        )
        installation_model = (
            (self._settings.ollama_model or "").strip() or None
            if installation_provider == "ollama"
            else None
        )
        installation_effective_provider = (
            installation_provider if installation_provider and installation_model else None
        )

        if provider_override is None:
            return LocalModelConfigurationResponse(
                mode=ModelConfigurationMode.INSTALLATION_DEFAULT,
                source=ModelConfigurationSource.INSTALLATION,
                selected_model=None,
                effective_provider=installation_effective_provider,
                effective_model=installation_model,
                installation_provider=installation_provider,
                installation_model=installation_model,
            )
        if provider_override == "none":
            return LocalModelConfigurationResponse(
                mode=ModelConfigurationMode.DISABLED,
                source=ModelConfigurationSource.PROFILE,
                selected_model=None,
                effective_provider=None,
                effective_model=None,
                installation_provider=installation_provider,
                installation_model=installation_model,
            )
        return LocalModelConfigurationResponse(
            mode=ModelConfigurationMode.OLLAMA,
            source=ModelConfigurationSource.PROFILE,
            selected_model=selected_model,
            effective_provider=selected_provider,
            effective_model=selected_model,
            installation_provider=installation_provider,
            installation_model=installation_model,
        )


def _valid_model_name(value: str) -> bool:
    if not value or len(value) > 255 or not value[0].isalnum():
        return False
    return all(character.isalnum() or character in "._:/-" for character in value)
