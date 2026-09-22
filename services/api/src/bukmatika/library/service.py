from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.domain import RightsEvidence
from bukmatika.library.domain import (
    AssetStatusResponse,
    EditionDossierResponse,
    LibraryItemResponse,
    LibraryResponse,
    WorkDossierResponse,
)
from bukmatika.persistence import session_scope
from bukmatika.persistence.library import (
    DossierIdentityConflict,
    DossierNotFound,
    LibraryRepository,
    LibraryTargetNotFound,
)
from bukmatika.persistence.models import Asset, LibraryEntry, RightsEvidenceRecord
from bukmatika.rights import RightsEngine

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class LibraryService:
    """Principal-scoped consumer library and catalog dossier read model."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory
        self._rights = RightsEngine()

    async def dossier_for_source(
        self,
        *,
        principal_id: UUID,
        provider: str,
        provider_record_id: str,
    ) -> WorkDossierResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            work_id = await repository.resolve_source_work_id(provider, provider_record_id)
            return await self._dossier(repository, principal_id=principal_id, work_id=work_id)

    async def dossier_for_work(
        self,
        *,
        principal_id: UUID,
        work_id: UUID,
    ) -> WorkDossierResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            return await self._dossier(repository, principal_id=principal_id, work_id=work_id)

    async def list_library(self, *, principal_id: UUID) -> LibraryResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            entries = await repository.library_entries(principal_id)
            items = [await self._library_item(repository, entry) for entry in entries]
            return LibraryResponse(items=items)

    async def save_work(self, *, principal_id: UUID, work_id: UUID) -> LibraryItemResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            entry = await repository.save_work(principal_id, work_id)
            return await self._library_item(repository, entry)

    async def save_edition(
        self,
        *,
        principal_id: UUID,
        edition_id: UUID,
    ) -> LibraryItemResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            entry = await repository.save_edition(principal_id, edition_id)
            return await self._library_item(repository, entry)

    async def _dossier(
        self,
        repository: LibraryRepository,
        *,
        principal_id: UUID,
        work_id: UUID,
    ) -> WorkDossierResponse:
        work = await repository.get_work(work_id)
        if work is None:
            raise DossierNotFound("Work is not in the canonical catalog")

        work_entry_id = await repository.work_library_entry_id(principal_id, work.id)
        editions = []
        for edition in await repository.editions_for_work(work.id):
            specific_entry_id = await repository.edition_library_entry_id(principal_id, edition.id)
            effective_entry_id = specific_entry_id or work_entry_id
            assets = [
                await self._asset_status(repository, asset)
                for asset in await repository.assets_for_edition(edition.id)
            ]
            editions.append(
                EditionDossierResponse(
                    edition_id=edition.id,
                    title=edition.title,
                    language=edition.language,
                    publication_year=edition.publication_year,
                    publisher=edition.publisher,
                    edition_statement=edition.edition_statement,
                    library_entry_id=effective_entry_id,
                    assets=assets,
                )
            )

        return WorkDossierResponse(
            work_id=work.id,
            title=work.canonical_title,
            authors=await repository.authors_for_work(work.id),
            subjects=await repository.subjects_for_work(work.id),
            work_library_entry_id=work_entry_id,
            editions=editions,
        )

    async def _asset_status(
        self,
        repository: LibraryRepository,
        asset: Asset,
    ) -> AssetStatusResponse:
        acquisition = await repository.acquisition_for_asset(asset.id)
        processing = await repository.processing_for_asset(asset.id)
        document = await repository.document_for_asset(asset.id)
        ocr_job = await repository.latest_ocr_job_for_asset(asset.id)
        rights_decision = await repository.latest_rights_decision_for_asset(asset.id)
        rights_state: str | None = None
        acquisition_allowed: bool | None = None
        if rights_decision is not None:
            rights_state = rights_decision.rights_state
            acquisition_allowed = bool(rights_decision.permissions.get("download", False))
        else:
            evidence_records = await repository.rights_evidence_for_asset(asset.id)
            evidence = [_rights_evidence(record) for record in evidence_records]
            policy = self._rights.decide(evidence)
            rights_state = policy.state.value
            acquisition_allowed = policy.unattended_acquisition_allowed

        return AssetStatusResponse(
            asset_id=asset.id,
            format=asset.format,
            media_type=asset.media_type,
            byte_size=asset.byte_size,
            stored=asset.stored_object_id is not None,
            acquisition_id=acquisition.id if acquisition is not None else None,
            acquisition_status=acquisition.status if acquisition is not None else None,
            processing_status=processing.status if processing is not None else None,
            processing_error_code=processing.error_code if processing is not None else None,
            ocr_job_id=ocr_job.id if ocr_job is not None else None,
            ocr_job_status=ocr_job.status if ocr_job is not None else None,
            document_id=document.id if document is not None else None,
            rights_state=rights_state,
            acquisition_allowed=acquisition_allowed,
        )

    async def _library_item(
        self,
        repository: LibraryRepository,
        entry: LibraryEntry,
    ) -> LibraryItemResponse:
        work = await repository.get_work(entry.work_id)
        if work is None:
            raise RuntimeError(f"Library entry {entry.id} references missing work")
        readable = await repository.readable_document_for_entry(entry)
        document_id = None
        readable_format = None
        progress_fraction = None
        reading_status = None
        if readable is not None:
            document, asset = readable
            document_id = document.id
            readable_format = asset.format
            state = await repository.reading_state_for_entry(entry.id, document.id)
            if state is not None:
                progress_fraction = state.progress_fraction
                reading_status = state.status
        return LibraryItemResponse(
            library_entry_id=entry.id,
            work_id=entry.work_id,
            edition_id=entry.edition_id,
            title=work.canonical_title,
            authors=await repository.authors_for_work(work.id),
            status=entry.status,
            readable_document_id=document_id,
            readable_format=readable_format,
            progress_fraction=progress_fraction,
            reading_status=reading_status,
        )


def _rights_evidence(record: RightsEvidenceRecord) -> RightsEvidence:
    return RightsEvidence.model_validate(
        {
            "state": record.state,
            "source": record.source,
            "basis": record.basis,
            "evidence_url": record.evidence_url,
            "license_uri": record.license_uri,
            "confidence": record.confidence,
        }
    )


__all__ = [
    "DossierIdentityConflict",
    "DossierNotFound",
    "LibraryService",
    "LibraryTargetNotFound",
]
