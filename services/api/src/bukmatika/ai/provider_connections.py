from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.credentials import DatabaseCredentialStore, InstallationCredentialKey
from bukmatika.ai.factory import build_provider_registry
from bukmatika.ai.provider_connection_domain import (
    ModelAssignmentListResponse,
    ModelAssignmentResponse,
    ModelAssignmentUpdate,
    ModelRole,
    ProviderCatalogResponse,
    ProviderConnectionCreate,
    ProviderConnectionListResponse,
    ProviderConnectionResponse,
    ProviderConnectionStatus,
    ProviderConnectionUpdate,
    ProviderCredentialUpdate,
    ProviderDescriptorResponse,
)
from bukmatika.ai.provider_registry import (
    ModelCapability,
    ProviderCapabilityUnavailable,
    ProviderNotRegistered,
    ProviderRegistry,
    RoutingType,
)
from bukmatika.config import Settings, get_settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.provider_models import AIModelAssignment, AIProviderConnection
from bukmatika.persistence.providers import (
    ProviderConnectionNotFound,
    ProviderConnectionRepository,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ProviderCredentialNotAllowed(ValueError):
    pass


class ProviderConnectionService:
    """Principal-scoped authority for provider connections, credentials, and model roles."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        registry: ProviderRegistry | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or build_provider_registry()
        self._session_scope = session_scope_factory

    def catalog(self) -> ProviderCatalogResponse:
        return ProviderCatalogResponse(
            providers=[
                ProviderDescriptorResponse(
                    provider_id=descriptor.provider_id,
                    display_name=descriptor.display_name,
                    routing_type=descriptor.routing_type,
                )
                for descriptor in self._registry.descriptors()
            ]
        )

    async def list_connections(self, *, principal_id: UUID) -> ProviderConnectionListResponse:
        async with self._session_scope() as database_session:
            repository = ProviderConnectionRepository(database_session)
            connections = await repository.list_connections(principal_id=principal_id)
            return ProviderConnectionListResponse(
                connections=[_connection_response(connection) for connection in connections]
            )

    async def list_assignments(self, *, principal_id: UUID) -> ModelAssignmentListResponse:
        async with self._session_scope() as database_session:
            repository = ProviderConnectionRepository(database_session)
            connections = {
                connection.id: connection
                for connection in await repository.list_connections(principal_id=principal_id)
            }
            assignments = await repository.list_assignments(principal_id=principal_id)
            responses: list[ModelAssignmentResponse] = []
            for assignment in assignments:
                connection = connections.get(assignment.connection_id)
                if connection is None or connection.status != "enabled":
                    raise ValueError("Active model assignment has no enabled provider connection")
                provider = self._registry.descriptor(connection.provider_id)
                if provider.routing_type.value != connection.routing_type:
                    raise ValueError("Provider connection routing metadata is inconsistent")
                model = self._registry.describe_model(
                    provider_id=connection.provider_id,
                    model_id=assignment.model_id,
                )
                responses.append(
                    _assignment_response(assignment, capabilities=model.capabilities)
                )
            return ModelAssignmentListResponse(assignments=responses)

    async def create_connection(
        self,
        *,
        principal_id: UUID,
        request: ProviderConnectionCreate,
    ) -> ProviderConnectionResponse:
        descriptor = self._registry.descriptor(request.provider_id.strip())
        async with self._session_scope() as database_session:
            connection = await ProviderConnectionRepository(database_session).create_connection(
                principal_id=principal_id,
                provider_id=descriptor.provider_id,
                routing_type=descriptor.routing_type.value,
                display_name=request.display_name,
            )
            await database_session.refresh(connection)
            return _connection_response(connection)

    async def update_connection(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        request: ProviderConnectionUpdate,
    ) -> ProviderConnectionResponse:
        async with self._session_scope() as database_session:
            connection = await ProviderConnectionRepository(database_session).update_connection(
                principal_id=principal_id,
                connection_id=connection_id,
                display_name=request.display_name,
                enabled=request.enabled,
            )
            await database_session.refresh(connection)
            return _connection_response(connection)

    async def disconnect(self, *, principal_id: UUID, connection_id: UUID) -> None:
        async with self._session_scope() as database_session:
            credentials = self._credential_store(database_session)
            repository = ProviderConnectionRepository(database_session)
            await repository.get_connection(
                principal_id=principal_id,
                connection_id=connection_id,
            )
            await credentials.delete(
                principal_id=principal_id,
                connection_id=connection_id,
            )
            await repository.disable_connection(
                principal_id=principal_id,
                connection_id=connection_id,
            )

    async def replace_credential(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        request: ProviderCredentialUpdate,
    ) -> ProviderConnectionResponse:
        async with self._session_scope() as database_session:
            repository = ProviderConnectionRepository(database_session)
            connection = await repository.get_connection(
                principal_id=principal_id,
                connection_id=connection_id,
            )
            if connection.routing_type != RoutingType.CLOUD.value:
                raise ProviderCredentialNotAllowed(
                    "Credentials are only accepted for cloud provider connections"
                )
            await self._credential_store(database_session).replace(
                principal_id=principal_id,
                connection_id=connection_id,
                secret=request.secret,
            )
            await database_session.refresh(connection)
            return _connection_response(connection)

    async def delete_credential(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> ProviderConnectionResponse:
        async with self._session_scope() as database_session:
            repository = ProviderConnectionRepository(database_session)
            connection = await repository.get_connection(
                principal_id=principal_id,
                connection_id=connection_id,
            )
            await self._credential_store(database_session).delete(
                principal_id=principal_id,
                connection_id=connection_id,
            )
            await database_session.refresh(connection)
            return _connection_response(connection)

    async def assign_model(
        self,
        *,
        principal_id: UUID,
        role: ModelRole,
        request: ModelAssignmentUpdate,
    ) -> ModelAssignmentResponse:
        async with self._session_scope() as database_session:
            repository = ProviderConnectionRepository(database_session)
            connection = await repository.get_connection(
                principal_id=principal_id,
                connection_id=request.connection_id,
                enabled_only=True,
            )
            descriptor = self._registry.descriptor(connection.provider_id)
            if descriptor.routing_type.value != connection.routing_type:
                raise ValueError("Provider connection routing metadata is inconsistent")
            capability = _required_capability(role)
            model = self._registry.require_capability(
                provider_id=connection.provider_id,
                model_id=request.model_id.strip(),
                capability=capability,
            )
            assignment = await repository.assign_model(
                principal_id=principal_id,
                connection_id=connection.id,
                role=role.value,
                model_id=model.model_id,
                priority=request.priority,
            )
            return _assignment_response(assignment, capabilities=model.capabilities)

    def _credential_store(self, database_session: AsyncSession) -> DatabaseCredentialStore:
        return DatabaseCredentialStore(
            database_session,
            installation_key=InstallationCredentialKey(self._settings.credential_key_path),
        )


def _required_capability(role: ModelRole) -> ModelCapability:
    if role is ModelRole.EMBEDDINGS:
        return ModelCapability.EMBEDDINGS
    return ModelCapability.STRUCTURED_GENERATION


def _connection_response(connection: AIProviderConnection) -> ProviderConnectionResponse:
    return ProviderConnectionResponse(
        connection_id=connection.id,
        provider_id=connection.provider_id,
        display_name=connection.display_name,
        routing_type=RoutingType(connection.routing_type),
        status=ProviderConnectionStatus(connection.status),
        credential_configured=connection.credential_reference is not None,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
    )


def _assignment_response(
    assignment: AIModelAssignment,
    *,
    capabilities: frozenset[ModelCapability],
) -> ModelAssignmentResponse:
    return ModelAssignmentResponse(
        assignment_id=assignment.id,
        connection_id=assignment.connection_id,
        role=ModelRole(assignment.role),
        model_id=assignment.model_id,
        priority=assignment.priority,
        enabled=assignment.enabled,
        capabilities=sorted(capabilities, key=lambda capability: capability.value),
    )


__all__ = [
    "ProviderCapabilityUnavailable",
    "ProviderConnectionNotFound",
    "ProviderConnectionService",
    "ProviderCredentialNotAllowed",
    "ProviderNotRegistered",
]
