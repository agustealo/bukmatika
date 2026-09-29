from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.provider_connection_domain import (
    ModelAssignmentUpdate,
    ModelRole,
    ProviderConnectionCreate,
)
from bukmatika.ai.provider_connections import ProviderConnectionService
from bukmatika.persistence.models import Principal


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(
        kind="local",
        external_subject=f"assignment-read-{suffix}-{uuid4()}",
    )
    session.add(principal)
    await session.flush()
    return principal


def _session_scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_active_assignment_reads_are_principal_scoped_and_rehydratable(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "owner")
    other = await _principal(session, "other")
    service = ProviderConnectionService(session_scope_factory=_session_scope(session))
    connection = await service.create_connection(
        principal_id=owner.id,
        request=ProviderConnectionCreate(provider_id="ollama"),
    )
    created = await service.assign_model(
        principal_id=owner.id,
        role=ModelRole.RESEARCH,
        request=ModelAssignmentUpdate(
            connection_id=connection.connection_id,
            model_id="llama3.2:latest",
        ),
    )

    owner_view = await service.list_assignments(principal_id=owner.id)
    assert len(owner_view.assignments) == 1
    assert owner_view.assignments[0].assignment_id == created.assignment_id
    assert owner_view.assignments[0].connection_id == connection.connection_id
    assert owner_view.assignments[0].role is ModelRole.RESEARCH
    assert owner_view.assignments[0].model_id == "llama3.2:latest"
    assert owner_view.assignments[0].enabled is True
    assert owner_view.assignments[0].capabilities

    other_view = await service.list_assignments(principal_id=other.id)
    assert other_view.assignments == []


async def test_disabled_connection_disappears_from_current_assignment_reads(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "disabled")
    service = ProviderConnectionService(session_scope_factory=_session_scope(session))
    connection = await service.create_connection(
        principal_id=principal.id,
        request=ProviderConnectionCreate(provider_id="ollama"),
    )
    await service.assign_model(
        principal_id=principal.id,
        role=ModelRole.PRIMARY,
        request=ModelAssignmentUpdate(
            connection_id=connection.connection_id,
            model_id="llama3.2:latest",
        ),
    )

    await service.disconnect(
        principal_id=principal.id,
        connection_id=connection.connection_id,
    )

    current = await service.list_assignments(principal_id=principal.id)
    assert current.assignments == []


async def test_clear_model_role_only_removes_that_principals_active_role(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "clear-owner")
    other = await _principal(session, "clear-other")
    service = ProviderConnectionService(session_scope_factory=_session_scope(session))

    owner_connection = await service.create_connection(
        principal_id=owner.id,
        request=ProviderConnectionCreate(provider_id="ollama"),
    )
    other_connection = await service.create_connection(
        principal_id=other.id,
        request=ProviderConnectionCreate(provider_id="ollama"),
    )
    await service.assign_model(
        principal_id=owner.id,
        role=ModelRole.RESEARCH,
        request=ModelAssignmentUpdate(
            connection_id=owner_connection.connection_id,
            model_id="owner-model",
        ),
    )
    await service.assign_model(
        principal_id=owner.id,
        role=ModelRole.PRIMARY,
        request=ModelAssignmentUpdate(
            connection_id=owner_connection.connection_id,
            model_id="owner-primary",
        ),
    )
    await service.assign_model(
        principal_id=other.id,
        role=ModelRole.RESEARCH,
        request=ModelAssignmentUpdate(
            connection_id=other_connection.connection_id,
            model_id="other-model",
        ),
    )

    await service.clear_model_role(principal_id=owner.id, role=ModelRole.RESEARCH)

    owner_view = await service.list_assignments(principal_id=owner.id)
    assert [(item.role, item.model_id) for item in owner_view.assignments] == [
        (ModelRole.PRIMARY, "owner-primary")
    ]
    other_view = await service.list_assignments(principal_id=other.id)
    assert [(item.role, item.model_id) for item in other_view.assignments] == [
        (ModelRole.RESEARCH, "other-model")
    ]
