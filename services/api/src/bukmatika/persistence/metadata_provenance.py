from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import MetadataAssertion, SourceObservation, SourceRecord


@dataclass(frozen=True, slots=True)
class MetadataProvenanceRecord:
    assertion: MetadataAssertion
    observation: SourceObservation
    source: SourceRecord


class MetadataProvenanceRepository:
    """Read authority for field-level catalog assertions and their exact source observations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assertions_for_entity(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
    ) -> list[MetadataProvenanceRecord]:
        rows = (
            await self._session.execute(
                select(MetadataAssertion, SourceObservation, SourceRecord)
                .join(
                    SourceObservation,
                    SourceObservation.id == MetadataAssertion.source_observation_id,
                )
                .join(SourceRecord, SourceRecord.id == SourceObservation.source_record_id)
                .where(
                    MetadataAssertion.entity_type == entity_type,
                    MetadataAssertion.entity_id == entity_id,
                )
                .order_by(
                    MetadataAssertion.field_name,
                    SourceRecord.provider,
                    SourceRecord.provider_record_id,
                    MetadataAssertion.created_at,
                    MetadataAssertion.id,
                )
            )
        ).all()
        return [
            MetadataProvenanceRecord(assertion=assertion, observation=observation, source=source)
            for assertion, observation, source in rows
        ]


__all__ = ["MetadataProvenanceRecord", "MetadataProvenanceRepository"]
