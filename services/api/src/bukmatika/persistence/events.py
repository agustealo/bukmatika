from datetime import datetime
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
    ACQUISITION_ATTEMPT_STARTED = "acquisition.attempt_started"
    ACQUISITION_RETRY_SCHEDULED = "acquisition.retry_scheduled"
    ACQUISITION_CANCEL_REQUESTED = "acquisition.cancel_requested"
    ACQUISITION_CANCELLED = "acquisition.cancelled"
    ACQUISITION_DENIED = "acquisition.denied"
    ACQUISITION_FAILED = "acquisition.failed"
    ACQUISITION_QUARANTINED = "acquisition.quarantined"
    ACQUISITION_STORED = "acquisition.stored"
    DOCUMENT_PROCESSING_STARTED = "document.processing_started"
    DOCUMENT_PROCESSING_COMPLETED = "document.processing_completed"
    DOCUMENT_PROCESSING_FAILED = "document.processing_failed"
    DOCUMENT_OCR_REQUESTED = "document.ocr_requested"
    DOCUMENT_OCR_STARTED = "document.ocr_started"
    DOCUMENT_OCR_RETRY_SCHEDULED = "document.ocr_retry_scheduled"
    DOCUMENT_OCR_COMPLETED = "document.ocr_completed"
    DOCUMENT_OCR_FAILED = "document.ocr_failed"
    DOCUMENT_SEARCH_SUBMITTED = "document.search_submitted"
    DOCUMENT_SEARCH_COMPLETED = "document.search_completed"
    RESEARCH_SEARCH_COMPLETED = "research.search_completed"
    RESEARCH_EVIDENCE_BUILT = "research.evidence_built"
    AI_MODEL_COMPLETED = "ai.model_completed"
    AI_MODEL_FAILED = "ai.model_failed"
    READER_OPENED = "reader.opened"
    READING_PROGRESS_UPDATED = "reader.progress_updated"
    BOOKMARK_ADDED = "reader.bookmark_added"
    BOOKMARK_REMOVED = "reader.bookmark_removed"
    PREFERENCE_SET = "personalization.preference_set"
    PREFERENCE_FORGOTTEN = "personalization.preference_forgotten"
    PERSONALIZATION_SETTINGS_UPDATED = "personalization.settings_updated"
    PERSONALIZATION_RESET = "personalization.reset"


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
        occurred_at: datetime | None = None,
    ) -> InteractionEvent:
        event = InteractionEvent(
            principal_id=principal_id,
            event_type=event_type.value,
            entity_type=entity_type,
            entity_id=entity_id,
            context=context or {},
        )
        if occurred_at is not None:
            event.occurred_at = occurred_at
        self._session.add(event)
        await self._session.flush()
        return event
