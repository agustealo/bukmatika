from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import InteractionEvent


class SemanticEventType(StrEnum):
    DISCOVERY_SEARCH_SUBMITTED = "discovery.search_submitted"
    DISCOVERY_SEARCH_COMPLETED = "discovery.search_completed"
    DISCOVERY_SEARCH_FAILED = "discovery.search_failed"
    CATALOG_SEARCH_SUBMITTED = "catalog.search_submitted"
    CATALOG_SEARCH_COMPLETED = "catalog.search_completed"
    ACQUISITION_REQUESTED = "acquisition.requested"
    ACQUISITION_DENIED = "acquisition.denied"
    ACQUISITION_FAILED = "acquisition.failed"
    ACQUISITION_QUARANTINED = "acquisition.quarantined"
    ACQUISITION_STORED = "acquisition.stored"


class InteractionEventRepository:
    """Append-only semantic product events for personalization evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        event_type: SemanticEventType,
        *,
        principal_id: UUID | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        context: dict[str, Any] | None = None,
    ) -> InteractionEvent:
        event = InteractionEvent(
            principal_id=principal_id,
            event_type=event_type.value,
            entity_type=entity_type,
            entity_id=entity_id,
            context=context or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event
