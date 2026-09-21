from typing import Literal

from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Asset, Edition, RightsEvidenceRecord, Work


def _record(
    *,
    source: str,
    record_id: str,
    record_kind: Literal["work", "edition"],
    title: str,
    author: str,
    identifiers: dict[str, list[str]] | None = None,
    include_asset: bool = False,
) -> DiscoveredRecord:
    assets = (
        [
            DiscoveredAsset(
                name="book.pdf",
                url=HttpUrl(f"https://example.org/{record_id}/book.pdf"),
                format="PDF",
                media_type="application/pdf",
                size_bytes=100,
            )
        ]
        if include_asset
        else []
    )
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=source,
            source_record_id=record_id,
            record_kind=record_kind,
            work_key=f"{source}:{record_id}",
            identifiers=identifiers or {},
            title=title,
            authors=[author],
            landing_url=HttpUrl(f"https://example.org/{record_id}"),
            assets=assets,
            formats=["PDF"] if include_asset else [],
            rights=[
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source=source,
                    basis="Test evidence",
                )
            ],
        ),
        source_payload={"id": record_id, "title": title, "author": author},
        parser_version="test-v1",
    )


async def test_exact_title_author_reuses_work_across_providers(session: AsyncSession) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        _record(
            source="provider-a",
            record_id="work-1",
            record_kind="work",
            title="A Shared Work",
            author="Jane Writer",
        )
    )
    await resolver.ingest(
        _record(
            source="provider-b",
            record_id="edition-1",
            record_kind="edition",
            title="A Shared Work",
            author="Jane Writer",
            identifiers={"provider-b": ["edition-1"]},
            include_asset=True,
        )
    )
    await session.commit()

    work_count = await session.scalar(select(func.count()).select_from(Work))
    edition_count = await session.scalar(select(func.count()).select_from(Edition))
    asset_count = await session.scalar(select(func.count()).select_from(Asset))
    evidence_count = await session.scalar(select(func.count()).select_from(RightsEvidenceRecord))
    assert work_count == 1
    assert edition_count == 1
    assert asset_count == 1
    assert evidence_count == 2


async def test_same_title_different_author_does_not_merge(session: AsyncSession) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        _record(
            source="provider-c",
            record_id="work-a",
            record_kind="work",
            title="Common Title",
            author="Author One",
        )
    )
    await resolver.ingest(
        _record(
            source="provider-d",
            record_id="work-b",
            record_kind="work",
            title="Common Title",
            author="Author Two",
        )
    )
    await session.commit()

    work_count = await session.scalar(
        select(func.count()).select_from(Work).where(Work.canonical_title == "Common Title")
    )
    assert work_count == 2
