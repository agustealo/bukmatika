from __future__ import annotations

import asyncio
from typing import Any

from pydantic import HttpUrl
from sqlalchemy import select

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence import session_scope
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Work

TITLE = "Browser Metadata Provenance Fixture"
ISBN = "9780000088888"


def _record(
    *,
    provider: str,
    record_id: str,
    year: int,
    publisher: str,
    private_note: str,
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


async def run() -> str:
    async with session_scope() as database_session:
        existing = await database_session.scalar(
            select(Work.id).where(Work.canonical_title == TITLE).limit(1)
        )
        if existing is not None:
            return str(existing)

        resolver = CatalogResolver(CatalogRepository(database_session))
        await resolver.ingest(
            _record(
                provider="browser-provider-a",
                record_id="edition-a",
                year=1900,
                publisher="First Browser Press",
                private_note="must never appear in the dossier",
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
        return str(work_id)


def main() -> None:
    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
