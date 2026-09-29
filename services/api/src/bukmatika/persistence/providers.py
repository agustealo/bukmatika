from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.provider_models import AIModelAssignment, AIProviderConnection


class ProviderConnectionNotFound(LookupError):
    pass


class ProviderAssignmentNotFound(LookupError):
    pass


class ProviderConnectionRepository:
    """Canonical principal-scoped persistence authority for model provider routing state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_connections(self, *, principal_id: UUID) -> list[AIProviderConnection]:
        rows = await self._session.scalars(
            select(AIProviderConnection)
            .where(AIProviderConnection.principal_id == principal_id)
            .order_by(AIProviderConnection.created_at, AIProviderConnection.id)
        )
        return list(rows)

    async def create_connection(
        self,
        *,
        principal_id: UUID,
        provider_id: str,
        routing_type: str,
        display_name: str | None = None,
        credential_reference: str | None = None,
    ) -> AIProviderConnection:
        normalized_provider = provider_id.strip()
        if not normalized_provider:
            raise ValueError("provider_id must not be empty")
        if routing_type not in ("local", "cloud"):
            raise ValueError("routing_type must be local or cloud")
        connection = AIProviderConnection(
            principal_id=principal_id,
            provider_id=normalized_provider,
            routing_type=routing_type,
            display_name=display_name.strip() if display_name and display_name.strip() else None,
            credential_reference=(
                credential_reference.strip()
                if credential_reference and credential_reference.strip()
                else None
            ),
            status="enabled",
        )
        self._session.add(connection)
        await self._session.flush()
        return connection

    async def get_connection(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        enabled_only: bool = False,
    ) -> AIProviderConnection:
        statement = select(AIProviderConnection).where(
            AIProviderConnection.id == connection_id,
            AIProviderConnection.principal_id == principal_id,
        )
        if enabled_only:
            statement = statement.where(AIProviderConnection.status == "enabled")
        connection = await self._session.scalar(statement)
        if connection is None:
            raise ProviderConnectionNotFound("Provider connection is unavailable")
        return connection

    async def update_connection(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        display_name: str | None,
        enabled: bool,
    ) -> AIProviderConnection:
        connection = await self.get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
        )
        connection.display_name = (
            display_name.strip() if display_name is not None and display_name.strip() else None
        )
        connection.status = "enabled" if enabled else "disabled"
        if not enabled:
            await self._session.execute(
                update(AIModelAssignment)
                .where(
                    AIModelAssignment.principal_id == principal_id,
                    AIModelAssignment.connection_id == connection_id,
                    AIModelAssignment.enabled.is_(True),
                )
                .values(enabled=False)
            )
        await self._session.flush()
        return connection

    async def assign_model(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        role: str,
        model_id: str,
        priority: int = 0,
    ) -> AIModelAssignment:
        if priority < 0:
            raise ValueError("priority must not be negative")
        normalized_role = role.strip()
        normalized_model = model_id.strip()
        if not normalized_role:
            raise ValueError("role must not be empty")
        if not normalized_model:
            raise ValueError("model_id must not be empty")
        await self.get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
            enabled_only=True,
        )
        existing = await self._session.scalar(
            select(AIModelAssignment).where(
                AIModelAssignment.principal_id == principal_id,
                AIModelAssignment.role == normalized_role,
                AIModelAssignment.priority == priority,
            )
        )
        if existing is None:
            assignment = AIModelAssignment(
                principal_id=principal_id,
                connection_id=connection_id,
                role=normalized_role,
                model_id=normalized_model,
                priority=priority,
                enabled=True,
            )
            self._session.add(assignment)
        else:
            existing.connection_id = connection_id
            existing.model_id = normalized_model
            existing.enabled = True
            existing.last_validated_at = None
            assignment = existing
        await self._session.flush()
        return assignment

    async def assignments_for_role(
        self,
        *,
        principal_id: UUID,
        role: str,
    ) -> list[AIModelAssignment]:
        normalized_role = role.strip()
        if not normalized_role:
            raise ValueError("role must not be empty")
        rows = await self._session.scalars(
            select(AIModelAssignment)
            .join(
                AIProviderConnection,
                (AIProviderConnection.id == AIModelAssignment.connection_id)
                & (AIProviderConnection.principal_id == AIModelAssignment.principal_id),
            )
            .where(
                AIModelAssignment.principal_id == principal_id,
                AIModelAssignment.role == normalized_role,
                AIModelAssignment.enabled.is_(True),
                AIProviderConnection.status == "enabled",
            )
            .order_by(AIModelAssignment.priority, AIModelAssignment.created_at)
        )
        return list(rows)

    async def primary_assignment(
        self,
        *,
        principal_id: UUID,
        role: str,
    ) -> AIModelAssignment:
        assignments = await self.assignments_for_role(principal_id=principal_id, role=role)
        if not assignments:
            raise ProviderAssignmentNotFound("No enabled model assignment is available")
        return assignments[0]

    async def disable_role_assignments(self, *, principal_id: UUID, role: str) -> None:
        normalized_role = role.strip()
        if not normalized_role:
            raise ValueError("role must not be empty")
        await self._session.execute(
            update(AIModelAssignment)
            .where(
                AIModelAssignment.principal_id == principal_id,
                AIModelAssignment.role == normalized_role,
                AIModelAssignment.enabled.is_(True),
            )
            .values(enabled=False)
        )
        await self._session.flush()

    async def disable_connection(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> AIProviderConnection:
        connection = await self.get_connection(
            principal_id=principal_id,
            connection_id=connection_id,
        )
        connection.status = "disabled"
        await self._session.execute(
            update(AIModelAssignment)
            .where(
                AIModelAssignment.principal_id == principal_id,
                AIModelAssignment.connection_id == connection_id,
                AIModelAssignment.enabled.is_(True),
            )
            .values(enabled=False)
        )
        await self._session.flush()
        return connection
