from __future__ import annotations

import asyncio
import hashlib
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    RightsEvidenceRecord,
    RightsEvidenceSubject,
    Work,
    WorkContributor,
)


async def seed(database_session: AsyncSession, principal_id: UUID) -> UUID:
    remote_url = f"https://example.invalid/browser-acquisition/{principal_id}/eligible.txt"
    existing_work_id = await database_session.scalar(
        select(Edition.work_id)
        .join(Asset, Asset.edition_id == Edition.id)
        .where(Asset.remote_url == remote_url)
        .limit(1)
    )
    if existing_work_id is not None:
        return existing_work_id

    suffix = principal_id.hex[:8]
    work = Work(
        canonical_title=f"Browser Acquisition Fixture {suffix}",
        normalized_title=f"browser acquisition fixture {suffix}",
    )
    contributor = Contributor(
        display_name="Browser Acquisition Author",
        normalized_name="browser acquisition author",
    )
    database_session.add_all((work, contributor))
    await database_session.flush()

    database_session.add(
        WorkContributor(
            work_id=work.id,
            contributor_id=contributor.id,
            role="author",
        )
    )
    edition = Edition(
        work_id=work.id,
        title="Browser Acquisition Edition",
        language="en",
        publication_year=1899,
        publisher="Bukmatika browser proof",
        edition_statement="Deterministic acquisition browser fixture",
    )
    database_session.add(edition)
    await database_session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=remote_url,
    )
    database_session.add(asset)
    await database_session.flush()

    evidence_digest = hashlib.sha256(
        f"browser-acquisition:{principal_id}:{asset.id}:public-domain".encode()
    ).hexdigest()
    evidence = RightsEvidenceRecord(
        evidence_sha256=evidence_digest,
        state="public_domain",
        source="browser-e2e",
        basis="Deterministic public-domain browser acquisition fixture.",
        evidence_url=(
            f"https://example.invalid/browser-acquisition/{principal_id}/rights"
        ),
        confidence=1.0,
    )
    database_session.add(evidence)
    await database_session.flush()
    database_session.add(
        RightsEvidenceSubject(
            rights_evidence_id=evidence.id,
            subject_type="asset",
            subject_id=asset.id,
        )
    )
    await database_session.flush()
    return work.id


async def run(principal_id: UUID) -> UUID:
    async with session_scope() as database_session:
        return await seed(database_session, principal_id)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_acquisition_seed.py <principal-id>")
    principal_id = UUID(sys.argv[1])
    work_id = asyncio.run(run(principal_id))
    print(work_id)


if __name__ == "__main__":
    main()
