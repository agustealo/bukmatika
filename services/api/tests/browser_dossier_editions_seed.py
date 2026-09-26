from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import HttpUrl
from sqlalchemy import select

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence import session_scope
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Work

TITLE = "Browser Multi-Edition Dossier Fixture"
AUTHOR = "Edition Acceptance Author"


def _record(
    *,
    provider: str,
    record_id: str,
    isbn: str,
    year: int,
    publisher: str,
) -> DiscoveredRecord:
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=provider,
            source_score=1.0,
            source_record_id=record_id,
            record_kind="edition",
            work_key=f"{provider}:{record_id}",
            identifiers={"isbn": [isbn]},
            title=TITLE,
            authors=[AUTHOR],
            first_publish_year=year,
            publisher=publisher,
            languages=["en"],
            landing_url=HttpUrl(f"https://example.invalid/{provider}/{record_id}"),
            formats=["PDF"],
            rights=[
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source=provider,
                    basis="Browser edition-picker fixture is metadata-only.",
                )
            ],
        ),
        source_payload={
            "id": record_id,
            "title": TITLE,
            "author": AUTHOR,
            "isbn": isbn,
            "year": year,
            "publisher": publisher,
        },
        parser_version="browser-edition-picker-v1",
    )


async def run() -> UUID:
    async with session_scope() as database_session:
        resolver = CatalogResolver(CatalogRepository(database_session))
        await resolver.ingest(
            _record(
                provider="browser-edition-provider-a",
                record_id="edition-1899",
                isbn="9780000018991",
                year=1899,
                publisher="First Browser Press",
            )
        )
        await resolver.ingest(
            _record(
                provider="browser-edition-provider-b",
                record_id="edition-1905",
                isbn="9780000019059",
                year=1905,
                publisher="Revised Browser Press",
            )
        )
        work_id = await database_session.scalar(
            select(Work.id).where(Work.canonical_title == TITLE).limit(1)
        )
        if work_id is None:
            raise RuntimeError("Browser multi-edition fixture failed to create a work")
        return work_id


def main() -> None:
    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
