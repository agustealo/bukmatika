from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TypeVar

import httpx
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.configuration import PrincipalModelRuntimeResolver
from bukmatika.ai.gateway import (
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
    ModelRequest,
)
from bukmatika.ai.provider_registry import (
    ModelCapability,
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRegistry,
    RoutingType,
)
from bukmatika.config import Settings
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.providers import ProviderConnectionRepository

StructuredResponseT = TypeVar("StructuredResponseT", bound=BaseModel)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _SyntheticCloudGateway:
    def __init__(self, model: str) -> None:
        self._identity = ModelProviderIdentity(
            provider="synthetic-cloud",
            model=model,
            routing="cloud",
        )

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        return ModelProviderReadiness(
            state=ModelReadinessState.READY,
            configured=True,
            ready=True,
            identity=self._identity,
        )

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[StructuredResponseT],
    ) -> StructuredResponseT:
        del request, response_type
        raise AssertionError("resolution proof must not send model content")


def _synthetic_registry() -> ProviderRegistry:
    def describe_model(model_id: str) -> ModelDescriptor:
        return ModelDescriptor(
            provider_id="synthetic-cloud",
            model_id=model_id,
            capabilities=frozenset({ModelCapability.STRUCTURED_GENERATION}),
        )

    def build_gateway(
        settings: Settings,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> _SyntheticCloudGateway:
        del settings, client
        return _SyntheticCloudGateway(model_id)

    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="synthetic-cloud",
                display_name="Synthetic Cloud",
                routing_type=RoutingType.CLOUD,
            ),
            describe_model=describe_model,
            model_gateway_factory=build_gateway,
        )
    )
    return registry


async def test_profile_runtime_uses_provider_assignment_not_legacy_ollama_identity(
    session: AsyncSession,
) -> None:
    principal = Principal(kind="local", external_subject="provider-neutral-runtime")
    session.add(principal)
    await session.flush()

    # During the additive migration the legacy field only signals that a profile override is
    # active. The provider-neutral assignment owns the actual provider/model selection.
    await PersonalizationRepository(session).update_model_configuration(
        principal_id=principal.id,
        provider_override="ollama",
        model_name_override="legacy-placeholder",
    )
    providers = ProviderConnectionRepository(session)
    connection = await providers.create_connection(
        principal_id=principal.id,
        provider_id="synthetic-cloud",
        routing_type="cloud",
        display_name="Synthetic Cloud",
        credential_reference="secret://synthetic/test",
    )
    await providers.assign_model(
        principal_id=principal.id,
        connection_id=connection.id,
        role="primary",
        model_id="reasoner-v1",
    )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    ) as client:
        runtime = await PrincipalModelRuntimeResolver(
            settings=Settings(model_provider="none"),
            client=client,
            session_scope_factory=_scope(session),
            provider_registry=_synthetic_registry(),
        ).resolve(principal_id=principal.id)

    assert runtime.gateway.identity is not None
    assert runtime.gateway.identity.provider == "synthetic-cloud"
    assert runtime.gateway.identity.model == "reasoner-v1"
    assert runtime.gateway.identity.routing == "cloud"
    assert runtime.configuration.effective_provider == "synthetic-cloud"
    assert runtime.configuration.effective_model == "reasoner-v1"
