from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.provider_connection_domain import (
    ModelAssignmentUpdate,
    ModelRole,
    ProviderConnectionCreate,
    ProviderConnectionUpdate,
    ProviderCredentialUpdate,
)
from bukmatika.ai.provider_connections import (
    ProviderCapabilityUnavailable,
    ProviderConnectionService,
    ProviderCredentialNotAllowed,
    ProviderNotRegistered,
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
from bukmatika.persistence.providers import ProviderConnectionNotFound, ProviderConnectionRepository


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(
        kind="local",
        external_subject=f"provider-connection-{suffix}-{uuid4()}",
    )
    session.add(principal)
    await session.flush()
    return principal


def _session_scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _cloud_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        ProviderRegistration(
            descriptor=ProviderDescriptor(
                provider_id="synthetic-cloud",
                display_name="Synthetic Cloud",
                routing_type=RoutingType.CLOUD,
            ),
            describe_model=lambda model_id: ModelDescriptor(
                provider_id="synthetic-cloud",
                model_id=model_id,
                capabilities=frozenset(
                    {
                        ModelCapability.TEXT_GENERATION,
                        ModelCapability.STRUCTURED_GENERATION,
                    }
                ),
            ),
        )
    )
    return registry


def _cloud_service(
    session: AsyncSession,
    tmp_path: Path,
) -> ProviderConnectionService:
    return ProviderConnectionService(
        settings=Settings(credential_key_path=tmp_path / "credential.key"),
        registry=_cloud_registry(),
        session_scope_factory=_session_scope(session),
    )


async def test_catalog_and_create_reject_unregistered_provider(session: AsyncSession) -> None:
    principal = await _principal(session, "unknown")
    service = ProviderConnectionService(session_scope_factory=_session_scope(session))

    catalog = service.catalog()
    assert [provider.provider_id for provider in catalog.providers] == ["ollama", "openai"]

    with pytest.raises(ProviderNotRegistered):
        await service.create_connection(
            principal_id=principal.id,
            request=ProviderConnectionCreate(provider_id="not-registered"),
        )


async def test_connection_response_never_exposes_credential_reference(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "safe-response")
    service = _cloud_service(session, tmp_path)
    connection = await service.create_connection(
        principal_id=principal.id,
        request=ProviderConnectionCreate(provider_id="synthetic-cloud"),
    )
    updated = await service.replace_credential(
        principal_id=principal.id,
        connection_id=connection.connection_id,
        request=ProviderCredentialUpdate(secret="super-secret-provider-key"),
    )

    payload = updated.model_dump(mode="json")
    assert payload["credential_configured"] is True
    assert "credential_reference" not in payload
    assert "secret" not in payload
    assert "super-secret-provider-key" not in str(payload)

    listed = await service.list_connections(principal_id=principal.id)
    assert listed.connections[0].credential_configured is True
    assert "credential_reference" not in listed.connections[0].model_dump()


async def test_local_provider_rejects_credentials(session: AsyncSession) -> None:
    principal = await _principal(session, "local-credential")
    service = ProviderConnectionService(session_scope_factory=_session_scope(session))
    connection = await service.create_connection(
        principal_id=principal.id,
        request=ProviderConnectionCreate(provider_id="ollama"),
    )

    with pytest.raises(ProviderCredentialNotAllowed):
        await service.replace_credential(
            principal_id=principal.id,
            connection_id=connection.connection_id,
            request=ProviderCredentialUpdate(secret="must-not-be-accepted"),
        )


async def test_cross_principal_connection_mutation_is_denied(session: AsyncSession) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    service = ProviderConnectionService(session_scope_factory=_session_scope(session))
    connection = await service.create_connection(
        principal_id=owner.id,
        request=ProviderConnectionCreate(provider_id="ollama"),
    )

    with pytest.raises(ProviderConnectionNotFound):
        await service.update_connection(
            principal_id=other.id,
            connection_id=connection.connection_id,
            request=ProviderConnectionUpdate(display_name="stolen", enabled=False),
        )


async def test_model_assignment_requires_role_capability(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "capability")
    service = _cloud_service(session, tmp_path)
    connection = await service.create_connection(
        principal_id=principal.id,
        request=ProviderConnectionCreate(provider_id="synthetic-cloud"),
    )

    assignment = await service.assign_model(
        principal_id=principal.id,
        role=ModelRole.PRIMARY,
        request=ModelAssignmentUpdate(
            connection_id=connection.connection_id,
            model_id="reasoner-v1",
        ),
    )
    assert assignment.role is ModelRole.PRIMARY
    assert ModelCapability.STRUCTURED_GENERATION in assignment.capabilities

    with pytest.raises(ProviderCapabilityUnavailable):
        await service.assign_model(
            principal_id=principal.id,
            role=ModelRole.EMBEDDINGS,
            request=ModelAssignmentUpdate(
                connection_id=connection.connection_id,
                model_id="reasoner-v1",
            ),
        )


async def test_disconnect_destroys_credential_and_disables_assignments(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session, "disconnect")
    service = _cloud_service(session, tmp_path)
    connection = await service.create_connection(
        principal_id=principal.id,
        request=ProviderConnectionCreate(provider_id="synthetic-cloud"),
    )
    await service.replace_credential(
        principal_id=principal.id,
        connection_id=connection.connection_id,
        request=ProviderCredentialUpdate(secret="disconnect-me"),
    )
    await service.assign_model(
        principal_id=principal.id,
        role=ModelRole.PRIMARY,
        request=ModelAssignmentUpdate(
            connection_id=connection.connection_id,
            model_id="reasoner-v1",
        ),
    )

    await service.disconnect(
        principal_id=principal.id,
        connection_id=connection.connection_id,
    )

    repository = ProviderConnectionRepository(session)
    persisted = await repository.get_connection(
        principal_id=principal.id,
        connection_id=connection.connection_id,
    )
    assert persisted.status == "disabled"
    assert persisted.credential_reference is None
    assert await repository.assignments_for_role(
        principal_id=principal.id,
        role=ModelRole.PRIMARY.value,
    ) == []
