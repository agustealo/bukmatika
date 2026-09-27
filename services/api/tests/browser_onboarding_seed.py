from __future__ import annotations

import asyncio
import json
import sys
from uuid import UUID

from bukmatika.persistence import session_scope
from bukmatika.persistence.models import Edition, LibraryEntry, Work


async def run(principal_id: UUID) -> dict[str, str]:
    async with session_scope() as database_session:
        title = f"Onboarding pending text {principal_id.hex[:8]}"
        work = Work(canonical_title=title, normalized_title=title.casefold())
        database_session.add(work)
        await database_session.flush()

        edition = Edition(
            work_id=work.id,
            title=f"{title} Edition",
            language="en",
            publication_year=1901,
            publisher="Bukmatika browser proof",
            edition_statement="Unprocessed onboarding fixture",
        )
        database_session.add(edition)
        await database_session.flush()

        entry = LibraryEntry(
            principal_id=principal_id,
            work_id=work.id,
            edition_id=edition.id,
            status="saved",
        )
        database_session.add(entry)
        await database_session.flush()

        return {
            "title": title,
            "library_entry_id": str(entry.id),
        }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_onboarding_seed.py <principal-id>")
    print(json.dumps(asyncio.run(run(UUID(sys.argv[1]))), sort_keys=True))


if __name__ == "__main__":
    main()
