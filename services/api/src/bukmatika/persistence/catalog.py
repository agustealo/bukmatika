import hashlib
import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import SourceObservation, SourceRecord


class CatalogRepository:
    """Canonical persistence operations for provider source evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_source_record(
        self,
        *,
        provider: str,
        provider_record_id: str,
        canonical_url: str,
    ) -> SourceRecord:
        statement = (
            insert(SourceRecord)
            .values(
                provider=provider,
                provider_record_id=provider_record_id,
                canonical_url=canonical_url,
            )
            .on_conflict_do_update(
                constraint="uq_source_provider_record",
                set_={"canonical_url": canonical_url, "updated_at": func.now()},
            )
            .returning(SourceRecord)
        )
        result = await self._session.execute(
            statement.execution_options(populate_existing=True)
        )
        return result.scalar_one()

    async def record_source_observation(
        self,
        *,
        source_record_id: Any,
        payload: dict[str, Any],
        parser_version: str,
    ) -> SourceObservation:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        statement = (
            insert(SourceObservation)
            .values(
                source_record_id=source_record_id,
                payload_sha256=digest,
                payload=payload,
                parser_version=parser_version,
                observation_count=1,
            )
            .on_conflict_do_update(
                constraint="uq_source_observation_payload",
                set_={
                    "last_observed_at": func.now(),
                    "observation_count": SourceObservation.observation_count + 1,
                    "parser_version": parser_version,
                },
            )
            .returning(SourceObservation)
        )
        result = await self._session.execute(
            statement.execution_options(populate_existing=True)
        )
        return result.scalar_one()
