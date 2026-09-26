from __future__ import annotations

import asyncio
from typing import Any

from PIL import Image
from pydantic import HttpUrl
from sqlalchemy import select

from bukmatika.catalog import CatalogResolver
from bukmatika.config import get_settings
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import (
    DiscoveredAsset,
    DiscoveredCover,
    DiscoveryCandidate,
    RightsEvidence,
    RightsState,
)
from bukmatika.library.covers import CoverCache
from bukmatika.persistence import session_scope
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import MetadataAssertion, Work

TITLE = "Browser Metadata Provenance Fixture"
ISBN = "9780000088888"
COVER_URL = "https://images.example.org/browser-cover.jpg"


def _record(
    *,
    provider: str,
    record_id: str,
    year: int,
    publisher: str,
    private_note: str,
    covers: list[DiscoveredCover] | None = None,
) -> DiscoveredRecord:
    payload: dict[str, Any] = {
        "id": record_id,
        "title": TITLE,
        "author": "Browser Provenance Author",
        "year": year,
        "publisher": publisher,
        "provider_private_note": private_note,
    }
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=provider,
            source_score=1.0,
            source_record_id=record_id,
            record_kind="edition",
            work_key=f"{provider}:{record_id}",
            identifiers={"isbn": [ISBN]},
            title=TITLE,
            authors=["Browser Provenance Author"],
            first_publish_year=year,
            publisher=publisher,
            landing_url=HttpUrl(f"https://example.invalid/{provider}/{record_id}"),
            formats=["PDF"],
            assets=[
                DiscoveredAsset(
                    name="book.pdf",
                    url=HttpUrl(f"https://example.invalid/{provider}/{record_id}/book.pdf"),
                    format="PDF",
                    media_type="application/pdf",
                    size_bytes=100,
                )
            ],
            covers=covers or [],
            rights=[
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source=provider,
                    basis="Browser provenance fixture does not authorize acquisition.",
                )
            ],
        ),
        source_payload=payload,
        parser_version="browser-provenance-v1",
    )


async def _seed_cached_cover(work_id: Any) -> None:
    async with session_scope() as database_session:
        assertion = await database_session.scalar(
            select(MetadataAssertion)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == work_id,
                MetadataAssertion.field_name == "covers",
                MetadataAssertion.normalization_method == "bukmatika-cover-normalize-v1",
            )
            .order_by(MetadataAssertion.created_at.asc())
            .limit(1)
        )
        if assertion is None:
            raise RuntimeError("Browser cover fixture has no canonical work cover assertion")

    cache = CoverCache(get_settings().storage_root)
    cache_key = f"{assertion.id}:{COVER_URL}"
    if await cache.existing(cache_key) is not None:
        return

    temp_path = await cache.create_temp_path("png")
    try:
        Image.new("RGB", (400, 600), (35, 82, 115)).save(temp_path, format="PNG")
        await cache.commit(temp_path, cache_key)
    finally:
        await cache.discard(temp_path)


async def run() -> str:
    async with session_scope() as database_session:
        resolver = CatalogResolver(CatalogRepository(database_session))
        await resolver.ingest(
            _record(
                provider="browser-provider-a",
                record_id="edition-a",
                year=1900,
                publisher="First Browser Press",
                private_note="must never appear in the dossier",
                covers=[
                    DiscoveredCover(
                        url=HttpUrl(COVER_URL),
                        kind="cover",
                        media_type="image/jpeg",
                        width=400,
                        height=600,
                    )
                ],
            )
        )
        await resolver.ingest(
            _record(
                provider="browser-provider-b",
                record_id="edition-b",
                year=1901,
                publisher="Revised Browser Press",
                private_note="must also never appear in the dossier",
            )
        )
        work_id = await database_session.scalar(
            select(Work.id).where(Work.canonical_title == TITLE).limit(1)
        )
        if work_id is None:
            raise RuntimeError("Browser metadata provenance fixture failed to create a work")

    await _seed_cached_cover(work_id)
    return str(work_id)


def main() -> None:
    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
