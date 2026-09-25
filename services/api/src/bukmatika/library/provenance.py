from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.domain import DossierIdentityConflict, DossierNotFound
from bukmatika.library.provenance_domain import (
    EditionMetadataProvenanceResponse,
    MetadataAssertionResponse,
    WorkMetadataProvenanceResponse,
)
from bukmatika.persistence import session_scope
from bukmatika.persistence.library import LibraryRepository
from bukmatika.persistence.metadata_provenance import (
    MetadataProvenanceRecord,
    MetadataProvenanceRepository,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class MetadataProvenanceService:
    """Read-only projection of canonical catalog assertions into dossier-safe provenance."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def dossier_for_source(
        self,
        *,
        provider: str,
        provider_record_id: str,
    ) -> WorkMetadataProvenanceResponse:
        async with self._session_scope() as database_session:
            library = LibraryRepository(database_session)
            work_id = await library.resolve_source_work_id(provider, provider_record_id)
            return await self._dossier(database_session, library=library, work_id=work_id)

    async def dossier_for_work(self, *, work_id: UUID) -> WorkMetadataProvenanceResponse:
        async with self._session_scope() as database_session:
            library = LibraryRepository(database_session)
            return await self._dossier(database_session, library=library, work_id=work_id)

    async def _dossier(
        self,
        database_session: AsyncSession,
        *,
        library: LibraryRepository,
        work_id: UUID,
    ) -> WorkMetadataProvenanceResponse:
        work = await library.get_work(work_id)
        if work is None:
            raise DossierNotFound("Work is not in the canonical catalog")

        provenance = MetadataProvenanceRepository(database_session)
        work_assertions = await provenance.assertions_for_entity(
            entity_type="work",
            entity_id=work.id,
        )
        editions = []
        for edition in await library.editions_for_work(work.id):
            edition_assertions = await provenance.assertions_for_entity(
                entity_type="edition",
                entity_id=edition.id,
            )
            editions.append(
                EditionMetadataProvenanceResponse(
                    edition_id=edition.id,
                    title=edition.title,
                    assertions=[_assertion_response(record) for record in edition_assertions],
                )
            )

        return WorkMetadataProvenanceResponse(
            work_id=work.id,
            title=work.canonical_title,
            assertions=[_assertion_response(record) for record in work_assertions],
            editions=editions,
        )


def _assertion_response(record: MetadataProvenanceRecord) -> MetadataAssertionResponse:
    assertion = record.assertion
    observation = record.observation
    source = record.source
    return MetadataAssertionResponse(
        field_name=assertion.field_name,
        value=assertion.value,
        provider=source.provider,
        provider_record_id=source.provider_record_id,
        source_url=source.canonical_url,
        confidence=assertion.confidence,
        normalization_method=assertion.normalization_method,
        parser_version=observation.parser_version,
        first_observed_at=observation.first_observed_at,
        last_observed_at=observation.last_observed_at,
        observation_count=observation.observation_count,
        assertion_created_at=assertion.created_at,
    )


__all__ = [
    "DossierIdentityConflict",
    "DossierNotFound",
    "MetadataProvenanceService",
]
