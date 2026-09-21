import os
from typing import Any

import pytest
from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import (
    Asset,
    Edition,
    MetadataAssertion,
    RightsEvidenceRecord,
    SourceObservation,
    SourceRecord,
    Work,
)

DATABASE_URL = os.getenv("BUKMATIKA_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    DATABASE_URL is None,
    reason="PostgreSQL integration URL not configured",
)


@pytest.fixture
async def session() -> AsyncSession:
    assert DATABASE_URL is not None
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        try:
            yield value
        finally:
            await value.rollback()
            await value.close()
    await engine.dispose()


def _edition_record(
    *,
    source: str,
    record_id: str,
    strong_id: str,
    year: int,
    publisher: str,
    payload_extra: dict[str, Any] | None = None,
) -> DiscoveredRecord:
    payload = {
        "id": record_id,
        "title": "Shared Historical Work",
        "author": "A. Historian",
        "year": year,
        "publisher": publisher,
    }
    if payload_extra:
        payload.update(payload_extra)
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=source,
            source_record_id=record_id,
            record_kind="edition",
            work_key=f"{source}:{record_id}",
            identifiers={"isbn": [strong_id]},
            title="Shared Historical Work",
            authors=["A. Historian"],
            first_publish_year=year,
            publisher=publisher,
            landing_url=HttpUrl(f"https://example.org/{record_id}"),
            formats=["PDF"],
            assets=[
                DiscoveredAsset(
                    name="book.pdf",
                    url=HttpUrl(f"https://example.org/{record_id}/book.pdf"),
                    format="PDF",
                    media_type="application/pdf",
                    size_bytes=100,
                )
            ],
            rights=[
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source=source,
                    basis="Rights require exact-asset review",
                )
            ],
        ),
        source_payload=payload,
        parser_version="test-v1",
    )


async def test_repeated_identical_ingest_is_idempotent(session: AsyncSession) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    record = _edition_record(
        source="provider-a",
        record_id="edition-a",
        strong_id="9780000000001",
        year=1900,
        publisher="Archive Press",
    )
    await resolver.ingest(record)
    await resolver.ingest(record)
    await session.flush()

    assert await session.scalar(select(func.count()).select_from(Work)) == 1
    assert await session.scalar(select(func.count()).select_from(Edition)) == 1
    assert await session.scalar(select(func.count()).select_from(Asset)) == 1
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(SourceObservation)) == 1
    assert await session.scalar(select(func.count()).select_from(RightsEvidenceRecord)) == 1
    observation = await session.scalar(select(SourceObservation))
    assert observation is not None
    assert observation.observation_count == 2


async def test_distinct_editions_share_one_work(session: AsyncSession) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        _edition_record(
            source="provider-a",
            record_id="edition-a",
            strong_id="9780000000002",
            year=1900,
            publisher="First Press",
        )
    )
    await resolver.ingest(
        _edition_record(
            source="provider-b",
            record_id="edition-b",
            strong_id="9780000000003",
            year=1910,
            publisher="Second Press",
        )
    )
    await session.flush()

    assert await session.scalar(select(func.count()).select_from(Work)) == 1
    assert await session.scalar(select(func.count()).select_from(Edition)) == 2


async def test_conflicting_edition_metadata_is_retained_as_assertions(
    session: AsyncSession,
) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        _edition_record(
            source="provider-a",
            record_id="edition-a",
            strong_id="9780000000004",
            year=1900,
            publisher="First Press",
        )
    )
    await resolver.ingest(
        _edition_record(
            source="provider-b",
            record_id="edition-b",
            strong_id="9780000000004",
            year=1901,
            publisher="Revised Press",
            payload_extra={"note": "conflicting provider metadata"},
        )
    )
    await session.flush()

    assert await session.scalar(select(func.count()).select_from(Edition)) == 1
    year_values = list(
        await session.scalars(
            select(MetadataAssertion.value).where(
                MetadataAssertion.entity_type == "edition",
                MetadataAssertion.field_name == "publication_year",
            )
        )
    )
    publisher_values = list(
        await session.scalars(
            select(MetadataAssertion.value).where(
                MetadataAssertion.entity_type == "edition",
                MetadataAssertion.field_name == "publisher",
            )
        )
    )
    assert set(year_values) == {1900, 1901}
    assert set(publisher_values) == {"First Press", "Revised Press"}
