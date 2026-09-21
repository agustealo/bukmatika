from pydantic import HttpUrl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.persistence.acquisition import AcquisitionRepository
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import (
    Asset,
    RightsEvidenceRecord,
    RightsEvidenceSubject,
    StoredObject,
)


async def test_provider_rights_evidence_is_bound_to_exact_asset(session: AsyncSession) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        DiscoveredRecord(
            candidate=DiscoveryCandidate(
                source="provider-a",
                source_record_id="edition-a",
                record_kind="edition",
                work_key="provider-a:work-a",
                identifiers={"isbn": ["9780000000100"]},
                title="Open Historical Work",
                authors=["A. Historian"],
                landing_url=HttpUrl("https://example.org/edition-a"),
                formats=["PDF"],
                assets=[
                    DiscoveredAsset(
                        name="book.pdf",
                        url=HttpUrl("https://example.org/edition-a/book.pdf"),
                        format="PDF",
                        media_type="application/pdf",
                        size_bytes=42,
                    )
                ],
                rights=[
                    RightsEvidence(
                        state=RightsState.OPEN_LICENSE,
                        source="provider-a",
                        basis="Exact item carries an open license.",
                        license_uri=HttpUrl("https://creativecommons.org/licenses/by/4.0/"),
                    )
                ],
            ),
            source_payload={"id": "edition-a"},
            parser_version="test-v1",
        )
    )
    await session.flush()

    asset = await session.scalar(select(Asset))
    evidence = await session.scalar(select(RightsEvidenceRecord))
    assert asset is not None
    assert evidence is not None
    link = await session.scalar(
        select(RightsEvidenceSubject).where(
            RightsEvidenceSubject.rights_evidence_id == evidence.id,
            RightsEvidenceSubject.subject_type == "asset",
            RightsEvidenceSubject.subject_id == asset.id,
        )
    )
    assert link is not None


async def test_stored_object_deduplicates_identical_content(session: AsyncSession) -> None:
    repository = AcquisitionRepository(session)
    first = await repository.upsert_stored_object(
        sha256="a" * 64,
        storage_key="objects/aa/aa/" + "a" * 64,
        byte_size=100,
        media_type="application/pdf",
    )
    second = await repository.upsert_stored_object(
        sha256="a" * 64,
        storage_key="objects/aa/aa/" + "a" * 64,
        byte_size=100,
        media_type="application/pdf",
    )
    await session.flush()

    assert first.id == second.id
    assert await session.scalar(select(func.count()).select_from(StoredObject)) == 1
