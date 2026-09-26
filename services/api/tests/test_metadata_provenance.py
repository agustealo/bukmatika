from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import DiscoveredAsset, DiscoveryCandidate, RightsEvidence, RightsState
from bukmatika.library.provenance import MetadataProvenanceService
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Work


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


def _edition_record(
    *,
    source: str,
    record_id: str,
    year: int,
    publisher: str,
    payload_extra: dict[str, Any] | None = None,
) -> DiscoveredRecord:
    payload: dict[str, Any] = {
        "id": record_id,
        "title": "Provenance Test Work",
        "author": "A. Source",
        "year": year,
        "publisher": publisher,
    }
    if payload_extra:
        payload.update(payload_extra)
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=source,
            source_score=1.0,
            source_record_id=record_id,
            record_kind="edition",
            work_key=f"{source}:{record_id}",
            identifiers={"isbn": ["9780000099999"]},
            title="Provenance Test Work",
            authors=["A. Source"],
            first_publish_year=year,
            publisher=publisher,
            landing_url=HttpUrl(f"https://example.org/{source}/{record_id}"),
            formats=["PDF"],
            assets=[
                DiscoveredAsset(
                    name="book.pdf",
                    url=HttpUrl(f"https://example.org/{source}/{record_id}/book.pdf"),
                    format="PDF",
                    media_type="application/pdf",
                    size_bytes=100,
                )
            ],
            rights=[
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source=source,
                    basis="Exact asset rights require review.",
                )
            ],
        ),
        source_payload=payload,
        parser_version="provenance-test-v1",
    )


async def _work_id(session: AsyncSession) -> UUID:
    work_id = await session.scalar(
        select(Work.id).where(Work.canonical_title == "Provenance Test Work")
    )
    assert work_id is not None
    return work_id


async def test_dossier_provenance_exposes_conflicting_assertions_without_raw_payload(
    session: AsyncSession,
) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        _edition_record(
            source="provider-a",
            record_id="edition-a",
            year=1900,
            publisher="First Press",
            payload_extra={"provider_private_note": "must not leak"},
        )
    )
    await resolver.ingest(
        _edition_record(
            source="provider-b",
            record_id="edition-b",
            year=1901,
            publisher="Revised Press",
            payload_extra={"provider_private_note": "also must not leak"},
        )
    )
    await session.flush()
    work_id = await _work_id(session)

    service = MetadataProvenanceService(session_scope_factory=_scope(session))
    dossier = await service.dossier_for_work(work_id=work_id)

    assert dossier.work_id == work_id
    assert dossier.title == "Provenance Test Work"
    assert len(dossier.editions) == 1

    edition = dossier.editions[0]
    year_assertions = [
        assertion for assertion in edition.assertions if assertion.field_name == "publication_year"
    ]
    publisher_assertions = [
        assertion for assertion in edition.assertions if assertion.field_name == "publisher"
    ]

    assert {assertion.value for assertion in year_assertions} == {1900, 1901}
    assert {assertion.provider for assertion in year_assertions} == {"provider-a", "provider-b"}
    assert {assertion.value for assertion in publisher_assertions} == {
        "First Press",
        "Revised Press",
    }
    assert all(assertion.confidence == 1.0 for assertion in edition.assertions)
    assert all(assertion.parser_version == "provenance-test-v1" for assertion in edition.assertions)
    assert all(assertion.observation_count == 1 for assertion in edition.assertions)
    assert all(
        assertion.source_url.startswith("https://example.org/")
        for assertion in edition.assertions
    )
    assert "provider_private_note" not in {
        assertion.field_name for assertion in dossier.assertions + edition.assertions
    }


async def test_source_identity_resolves_same_provenance_projection(session: AsyncSession) -> None:
    resolver = CatalogResolver(CatalogRepository(session))
    await resolver.ingest(
        _edition_record(
            source="provider-source",
            record_id="edition-source",
            year=1912,
            publisher="Source Press",
        )
    )
    await session.flush()
    work_id = await _work_id(session)

    service = MetadataProvenanceService(session_scope_factory=_scope(session))
    by_work = await service.dossier_for_work(work_id=work_id)
    by_source = await service.dossier_for_source(
        provider="provider-source",
        provider_record_id="edition-source",
    )

    assert by_work.work_id == work_id
    assert by_source == by_work
