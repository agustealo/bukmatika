"""Scoped storage for factual WordPress health-check observations."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.persistence.plugin_health_models import PluginHealthReceipt
from bukmatika.plugins.connection_health import ConnectionHealth


class PluginHealthReceiptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        state: ConnectionHealth,
        evidence_code: str,
        checked_at: datetime,
    ) -> PluginHealthReceipt:
        if state not in {ConnectionHealth.VERIFIED, ConnectionHealth.UNAVAILABLE}:
            raise ValueError("Only observed health states may be recorded")
        if checked_at.tzinfo is None or not evidence_code or len(evidence_code) > 64:
            raise ValueError("Health evidence requires a timestamp and bounded code")
        if not all(char.isascii() and (char.isalnum() or char == "_") for char in evidence_code):
            raise ValueError("Health evidence must be a non-sensitive code")
        connection = await self._session.scalar(
            select(PluginConnection.id).where(
                PluginConnection.id == connection_id,
                PluginConnection.principal_id == principal_id,
            )
        )
        if connection is None:
            raise LookupError("Connection is unavailable")
        receipt = PluginHealthReceipt(
            principal_id=principal_id,
            connection_id=connection_id,
            state=state.value,
            evidence_code=evidence_code,
            checked_at=checked_at.astimezone(timezone.utc),
        )
        self._session.add(receipt)
        await self._session.flush()
        return receipt

    async def latest(
        self, *, principal_id: UUID, connection_id: UUID
    ) -> PluginHealthReceipt | None:
        return await self._session.scalar(
            select(PluginHealthReceipt)
            .where(
                PluginHealthReceipt.principal_id == principal_id,
                PluginHealthReceipt.connection_id == connection_id,
            )
            .order_by(PluginHealthReceipt.checked_at.desc(), PluginHealthReceipt.id.desc())
            .limit(1)
        )
