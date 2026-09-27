from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.persistence.provider_models import AIModelAssignment
from bukmatika.persistence.providers import (
    ProviderAssignmentNotFound,
    ProviderConnectionNotFound,
    ProviderConnectionRepository,
)


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"provider-persistence-{suffix}-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


async def test_assignments_are_principal_owned_and_connection_disable_invalidates_them(
    session: AsyncSession,
) -> None:
    first = await _principal(session, "first")
    second = await _principal(session, "second")
    repository = ProviderConnectionRepository(session)

    connection = await repository.create_connection(
        principal_id=first.id,
        provider_id="ollama",
        routing_type="local",
        display_name="Ollama",
    )
    assignment = await repository.assign_model(
        principal_id=first.id,
        connection_id=connection.id,
        role="primary",
        model_id="qwen3:8b",
    )

    resolved = await repository.primary_assignment(principal_id=first.id, role="primary")
    assert resolved.id == assignment.id
    assert resolved.connection_id == connection.id

    with pytest.raises(ProviderConnectionNotFound):
        await repository.assign_model(
            principal_id=second.id,
            connection_id=connection.id,
            role="primary",
            model_id="should-not-cross-principals",
        )

    await repository.disable_connection(principal_id=first.id, connection_id=connection.id)

    with pytest.raises(ProviderAssignmentNotFound):
        await repository.primary_assignment(principal_id=first.id, role="primary")

    await session.refresh(assignment)
    assert assignment.enabled is False


async def test_database_rejects_cross_principal_connection_reference(
    session: AsyncSession,
) -> None:
    first = await _principal(session, "constraint-first")
    second = await _principal(session, "constraint-second")
    repository = ProviderConnectionRepository(session)
    connection = await repository.create_connection(
        principal_id=first.id,
        provider_id="synthetic",
        routing_type="cloud",
    )

    session.add(
        AIModelAssignment(
            principal_id=second.id,
            connection_id=connection.id,
            role="primary",
            model_id="synthetic-v1",
            priority=0,
            enabled=True,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_connection_rows_store_only_credential_references_not_secret_material(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "credential-reference")
    repository = ProviderConnectionRepository(session)
    connection = await repository.create_connection(
        principal_id=principal.id,
        provider_id="future-cloud",
        routing_type="cloud",
        credential_reference="credential-store://principal/provider-1",
    )

    assert connection.credential_reference == "credential-store://principal/provider-1"
    assert not hasattr(connection, "api_key")
    assert not hasattr(connection, "secret")
