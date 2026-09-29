from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.configuration import PrincipalModelRuntimeResolver
from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
)
from bukmatika.ai.provider_registry import (
    ModelCapability,
    ModelDescriptor,
    ProviderDescriptor,
    ProviderRegistration,
    ProviderRegistry,
    RoutingType,
)
from bukmatika.ai.routing_policy import (
    CloudEgressPolicy,
    ModelProviderPolicyDenied,
    PolicyEnforcedModelGateway,
)
from bukmatika.config import Settings
from bukmatika.persistence.models import Principal
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.providers import ProviderConnectionRepository
from bukmatika.persistence.routing_policy import AIRoutingPolicyRepository


class _Response(BaseModel):
    value: str


class _CountingGateway:
    def __init__(self, *, routing: str = "cloud") -> None:
        self.readiness_calls = 0
        self.generation_calls = 0
        self._identity = ModelProviderIdentity(
            provider="synthetic-cloud",
            model="reasoner-v1",
            routing=routing,
        )

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        self.readiness_calls += 1
        return ModelProviderReadiness(
            state=ModelReadinessState.READY,
            configured=True,
            ready=True,
            identity=self.identity,
        )

    async def generate_structured(
        self,
        request: ModelRequest,
        response_type: type[_Response],
    ) -> _Response:
        del request
        self.generation_calls += 1
        return response_type(value="ok")


def _request(classification: ModelDataClassification) -> ModelRequest:
    return ModelRequest(
        task=ModelTask.RUNTIME_SMOKE,
        payload={"probe": "safe"},
        data_classification=classification,
        max_output_tokens=32,
        timeout_seconds=1,
    )


async def test_local_only_cloud_gateway_denies_before_provider_io() -> None:
    delegate = _CountingGateway()
    gateway = PolicyEnforcedModelGateway(
        gateway=delegate,
        routing_type=RoutingType.CLOUD,
        cloud_egress_policy=CloudEgressPolicy.LOCAL_ONLY,
    )

    readiness = await gateway.readiness()
    assert readiness.ready is False
    assert delegate.readiness_calls == 0

    with pytest.raises(ModelProviderPolicyDenied):
        await gateway.generate_structured(
            _request(ModelDataClassification.PUBLIC),
            _Response,
        )
    assert delegate.generation_calls == 0


async def test_public_only_denies_private_context_but_allows_public() -> None:
    delegate = _CountingGateway()
    gateway = PolicyEnforcedModelGateway(
        gateway=delegate,
        routing_type=RoutingType.CLOUD,
        cloud_egress_policy=CloudEgressPolicy.PUBLIC_ONLY,
    )

    with pytest.raises(ModelProviderPolicyDenied):
        await gateway.generate_structured(
            _request(ModelDataClassification.PRIVATE_USER_CONTEXT),
            _Response,
        )
    assert delegate.generation_calls == 0

    response = await gateway.generate_structured(
        _request(ModelDataClassification.PUBLIC),
        _Response,
    )
    assert response.value == "ok"
    assert delegate.generation_calls == 1


async def test_local_provider_is_not_blocked_by_cloud_egress_policy() -> None:
    delegate = _CountingGateway(routing="local")
    gateway = PolicyEnforcedModelGateway(
        gateway=delegate,
        routing_type=RoutingType.LOCAL,
        cloud_egress_policy=CloudEgressPolicy.LOCAL_ONLY,
    )

    response = await gateway.generate_structured(
        _request(ModelDataClassification.PRIVATE_USER_CONTEXT),
        _Response,
    )
    assert response.value == "ok"
    assert delegate.generation_calls == 1


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"routing-{suffix}-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


async def test_profile_cloud_assignment_local_only_never_builds_provider_gateway(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "local-only")
    personalization = PersonalizationRepository(session)
    await personalization.update_model_configuration(
        principal_id=principal.id,
        provider_override="ollama",
        model_name_override="legacy-local-model",
    )
    repository = ProviderConnectionRepository(session)
    connection = await repository.create_connection(
        principal_id=principal.id,
        provider_id="synthetic-cloud",
        routing_type="cloud",
        display_name="Synthetic Cloud",
    )
    await repository.assign_model(
        principal_id=principal.id,
        connection_id=connection.id,
        role="primary",
        model_id="reasoner-v1",
    )
    policy = await AIRoutingPolicyRepository(session).get_or_create(principal_id=principal.id)
    policy.model_selection_mode = "profile"
    policy.cloud_egress_policy = "local_only"
    await session.flush()

    builds = 0

    def describe(model_id: str) -> ModelDescriptor:
        return ModelDescriptor(
            provider_id="synthetic-cloud",
            model_id=model_id,
            capabilities=frozenset({ModelCapability.STRUCTURED_GENERATION}),
        )

    def build(
        settings: Settings,
        client: httpx.AsyncClient,
        model_id: str,
    ) -> _CountingGateway:
        nonlocal builds
        del settings, client, model_id
        builds += 1
        return _CountingGateway()

    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="synthetic-cloud",
                display_name="Synthetic Cloud",
                routing_type=RoutingType.CLOUD,
            ),
            describe_model=describe,
            model_gateway_factory=build,
        )
    )

    @asynccontextmanager
    async def scope():  # type: ignore[no-untyped-def]
        yield session

    resolver = PrincipalModelRuntimeResolver(
        settings=Settings(credential_key_path=tmp_path / "unused.key"),
        session_scope_factory=scope,
        provider_registry=registry,
    )
    runtime = await resolver.resolve(principal_id=principal.id)

    assert runtime.gateway.identity is not None
    assert runtime.gateway.identity.provider == "synthetic-cloud"
    readiness = await runtime.gateway.readiness()
    assert readiness.ready is False
    assert builds == 0


async def test_ai_disabled_stops_before_profile_provider_construction(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "disabled")
    personalization = PersonalizationRepository(session)
    user_model = await personalization.update_model_configuration(
        principal_id=principal.id,
        provider_override="ollama",
        model_name_override="legacy-local-model",
    )
    user_model.ai_enabled = False
    policy = await AIRoutingPolicyRepository(session).get_or_create(principal_id=principal.id)
    policy.model_selection_mode = "profile"
    await session.flush()

    registry = ProviderRegistry()

    @asynccontextmanager
    async def scope():  # type: ignore[no-untyped-def]
        yield session

    runtime = await PrincipalModelRuntimeResolver(
        settings=Settings(),
        session_scope_factory=scope,
        provider_registry=registry,
    ).resolve(principal_id=principal.id)

    assert runtime.gateway.identity is None
    readiness = await runtime.gateway.readiness()
    assert readiness.configured is False
