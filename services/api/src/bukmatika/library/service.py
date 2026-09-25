import unicodedata
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import AcquisitionRequestStatus, AcquisitionStatus
from bukmatika.domain import RightsEvidence
from bukmatika.library.domain import (
    AssetStatusResponse,
    CollectionCreate,
    CollectionResponse,
    CollectionSummaryResponse,
    CollectionUpdate,
    EditionDossierResponse,
    LibraryItemResponse,
    LibraryOrganizationResponse,
    LibraryReadingStatus,
    LibraryResponse,
    SmartShelfContentsResponse,
    SmartShelfCreate,
    SmartShelfResponse,
    SmartShelfRule,
    SmartShelfUpdate,
    TagAssignRequest,
    TagResponse,
    TagSummaryResponse,
    TagUpdate,
    WorkDossierResponse,
)
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition_requests import AcquisitionRequestRepository
from bukmatika.persistence.library import (
    DossierIdentityConflict,
    DossierNotFound,
    LibraryRepository,
    LibraryTargetNotFound,
)
from bukmatika.persistence.library_organization import (
    LibraryOrganizationConflict,
    LibraryOrganizationNotFound,
    LibraryOrganizationRepository,
)
from bukmatika.persistence.library_organization_models import LibrarySmartShelf
from bukmatika.persistence.library_resume import LibraryResumeRepository
from bukmatika.persistence.models import Acquisition, Asset, LibraryEntry, RightsEvidenceRecord
from bukmatika.rights import RightsEngine

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class LibraryService:
    """Principal-scoped consumer library, dossier, and organization authority."""

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
            return await self._dossier(
                database_session,
                repository,
                principal_id=principal_id,
                work_id=work_id,
            )

    async def dossier_for_work(
        self,
        *,
        principal_id: UUID,
        work_id: UUID,
    ) -> WorkDossierResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            return await self._dossier(
                database_session,
                repository,
                principal_id=principal_id,
                work_id=work_id,
            )

    async def list_library(
        self,
        *,
        principal_id: UUID,
        reading_status: LibraryReadingStatus | None = None,
        collection_id: UUID | None = None,
        tag_id: UUID | None = None,
    ) -> LibraryResponse:
        async with self._session_scope() as database_session:
            return await self._filtered_library(
                database_session,
                principal_id=principal_id,
                reading_status=reading_status,
                collection_id=collection_id,
                tag_id=tag_id,
            )

    async def organization(self, *, principal_id: UUID) -> LibraryOrganizationResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            collections = await repository.collections(principal_id)
            tags = await repository.tags(principal_id)
            smart_shelves = await repository.smart_shelves(principal_id)
            rendered_shelves = [
                await self._smart_shelf_response(
                    database_session,
                    principal_id=principal_id,
                    shelf=shelf,
                )
                for shelf in smart_shelves
            ]
            return LibraryOrganizationResponse(
                collections=[
                    CollectionResponse(
                        collection_id=collection.id,
                        name=collection.name,
                        description=collection.description,
                        item_count=count,
                    )
                    for collection, count in collections
                ],
                tags=[
                    TagResponse(tag_id=tag.id, name=tag.name, item_count=count)
                    for tag, count in tags
                ],
                smart_shelves=rendered_shelves,
            )

    async def create_collection(
        self,
        *,
        principal_id: UUID,
        create: CollectionCreate,
    ) -> CollectionResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            collection = await repository.create_collection(
                principal_id=principal_id,
                name=create.name,
                normalized_name=_normalized_key(create.name),
                description=create.description,
            )
            counts = dict(
                (item.id, count) for item, count in await repository.collections(principal_id)
            )
            return CollectionResponse(
                collection_id=collection.id,
                name=collection.name,
                description=collection.description,
                item_count=counts.get(collection.id, 0),
            )

    async def update_collection(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
        update: CollectionUpdate,
    ) -> CollectionResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            collection = await repository.update_collection(
                principal_id=principal_id,
                collection_id=collection_id,
                name=update.name,
                normalized_name=_normalized_key(update.name),
                description=update.description,
            )
            counts = dict(
                (item.id, count) for item, count in await repository.collections(principal_id)
            )
            return CollectionResponse(
                collection_id=collection.id,
                name=collection.name,
                description=collection.description,
                item_count=counts.get(collection.id, 0),
            )

    async def delete_collection(self, *, principal_id: UUID, collection_id: UUID) -> None:
        async with self._session_scope() as database_session:
            await LibraryOrganizationRepository(database_session).delete_collection(
                principal_id=principal_id,
                collection_id=collection_id,
            )

    async def add_collection_entry(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
        library_entry_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            await LibraryOrganizationRepository(database_session).add_collection_entry(
                principal_id=principal_id,
                collection_id=collection_id,
                library_entry_id=library_entry_id,
            )

    async def remove_collection_entry(
        self,
        *,
        principal_id: UUID,
        collection_id: UUID,
        library_entry_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            await LibraryOrganizationRepository(database_session).remove_collection_entry(
                principal_id=principal_id,
                collection_id=collection_id,
                library_entry_id=library_entry_id,
            )

    async def assign_tag(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        request: TagAssignRequest,
    ) -> TagResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            tag = await repository.assign_tag(
                principal_id=principal_id,
                library_entry_id=library_entry_id,
                name=request.name,
                normalized_name=_normalized_key(request.name),
            )
            counts = dict((item.id, count) for item, count in await repository.tags(principal_id))
            return TagResponse(tag_id=tag.id, name=tag.name, item_count=counts.get(tag.id, 0))

    async def update_tag(
        self,
        *,
        principal_id: UUID,
        tag_id: UUID,
        update: TagUpdate,
    ) -> TagResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            tag = await repository.update_tag(
                principal_id=principal_id,
                tag_id=tag_id,
                name=update.name,
                normalized_name=_normalized_key(update.name),
            )
            counts = dict((item.id, count) for item, count in await repository.tags(principal_id))
            return TagResponse(tag_id=tag.id, name=tag.name, item_count=counts.get(tag.id, 0))

    async def remove_entry_tag(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        tag_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            await LibraryOrganizationRepository(database_session).remove_entry_tag(
                principal_id=principal_id,
                library_entry_id=library_entry_id,
                tag_id=tag_id,
            )

    async def delete_tag(self, *, principal_id: UUID, tag_id: UUID) -> None:
        async with self._session_scope() as database_session:
            await LibraryOrganizationRepository(database_session).delete_tag(
                principal_id=principal_id,
                tag_id=tag_id,
            )

    async def create_smart_shelf(
        self,
        *,
        principal_id: UUID,
        create: SmartShelfCreate,
    ) -> SmartShelfResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            shelf = await repository.create_smart_shelf(
                principal_id=principal_id,
                name=create.name,
                normalized_name=_normalized_key(create.name),
                description=create.description,
                reading_status=_reading_status_value(create.rule.reading_status),
                collection_id=create.rule.collection_id,
                tag_id=create.rule.tag_id,
            )
            return await self._smart_shelf_response(
                database_session,
                principal_id=principal_id,
                shelf=shelf,
            )

    async def update_smart_shelf(
        self,
        *,
        principal_id: UUID,
        smart_shelf_id: UUID,
        update: SmartShelfUpdate,
    ) -> SmartShelfResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            shelf = await repository.update_smart_shelf(
                principal_id=principal_id,
                smart_shelf_id=smart_shelf_id,
                name=update.name,
                normalized_name=_normalized_key(update.name),
                description=update.description,
                reading_status=_reading_status_value(update.rule.reading_status),
                collection_id=update.rule.collection_id,
                tag_id=update.rule.tag_id,
            )
            return await self._smart_shelf_response(
                database_session,
                principal_id=principal_id,
                shelf=shelf,
            )

    async def delete_smart_shelf(
        self,
        *,
        principal_id: UUID,
        smart_shelf_id: UUID,
    ) -> None:
        async with self._session_scope() as database_session:
            await LibraryOrganizationRepository(database_session).delete_smart_shelf(
                principal_id=principal_id,
                smart_shelf_id=smart_shelf_id,
            )

    async def smart_shelf_contents(
        self,
        *,
        principal_id: UUID,
        smart_shelf_id: UUID,
    ) -> SmartShelfContentsResponse:
        async with self._session_scope() as database_session:
            repository = LibraryOrganizationRepository(database_session)
            shelf = await repository.require_smart_shelf(principal_id, smart_shelf_id)
            library = await self._filtered_library(
                database_session,
                principal_id=principal_id,
                reading_status=_reading_status(shelf.reading_status),
                collection_id=shelf.collection_id,
                tag_id=shelf.tag_id,
            )
            return SmartShelfContentsResponse(
                shelf=_smart_shelf_response(shelf, len(library.items)),
                items=library.items,
            )

    async def save_work(self, *, principal_id: UUID, work_id: UUID) -> LibraryItemResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            entry = await repository.save_work(principal_id, work_id)
            return await self._library_item(
                repository,
                LibraryResumeRepository(database_session),
                entry,
            )

    async def save_edition(
        self,
        *,
        principal_id: UUID,
        edition_id: UUID,
    ) -> LibraryItemResponse:
        async with self._session_scope() as database_session:
            repository = LibraryRepository(database_session)
            entry = await repository.save_edition(principal_id, edition_id)
            return await self._library_item(
                repository,
                LibraryResumeRepository(database_session),
                entry,
            )

    async def _filtered_library(
        self,
        database_session: AsyncSession,
        *,
        principal_id: UUID,
        reading_status: LibraryReadingStatus | None,
        collection_id: UUID | None,
        tag_id: UUID | None,
    ) -> LibraryResponse:
        repository = LibraryRepository(database_session)
        organization = LibraryOrganizationRepository(database_session)
        resume = LibraryResumeRepository(database_session)
        entries = await repository.library_entries(principal_id)

        if collection_id is not None:
            allowed = await organization.collection_entry_ids(
                principal_id=principal_id,
                collection_id=collection_id,
            )
            entries = [entry for entry in entries if entry.id in allowed]
        if tag_id is not None:
            allowed = await organization.tag_entry_ids(
                principal_id=principal_id,
                tag_id=tag_id,
            )
            entries = [entry for entry in entries if entry.id in allowed]

        items = [await self._library_item(repository, resume, entry) for entry in entries]
        if reading_status is not None:
            items = [
                item
                for item in items
                if _effective_reading_status(item) == reading_status.value
            ]

        collection_map, tag_map = await organization.organization_for_entries(
            principal_id=principal_id,
            entry_ids=[item.library_entry_id for item in items],
        )
        return LibraryResponse(
            items=[
                item.model_copy(
                    update={
                        "collections": [
                            CollectionSummaryResponse(
                                collection_id=collection.id,
                                name=collection.name,
                            )
                            for collection in collection_map.get(item.library_entry_id, [])
                        ],
                        "tags": [
                            TagSummaryResponse(tag_id=tag.id, name=tag.name)
                            for tag in tag_map.get(item.library_entry_id, [])
                        ],
                    }
                )
                for item in items
            ]
        )

    async def _smart_shelf_response(
        self,
        database_session: AsyncSession,
        *,
        principal_id: UUID,
        shelf: LibrarySmartShelf,
    ) -> SmartShelfResponse:
        library = await self._filtered_library(
            database_session,
            principal_id=principal_id,
            reading_status=_reading_status(shelf.reading_status),
            collection_id=shelf.collection_id,
            tag_id=shelf.tag_id,
        )
        return _smart_shelf_response(shelf, len(library.items))

    async def _dossier(
        self,
        database_session: AsyncSession,
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
                await self._asset_status(
                    database_session,
                    repository,
                    principal_id=principal_id,
                    asset=asset,
                )
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
        database_session: AsyncSession,
        repository: LibraryRepository,
        *,
        principal_id: UUID,
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

        principal_request = await AcquisitionRequestRepository(database_session).request_for_asset(
            principal_id=principal_id,
            asset_id=asset.id,
        )
        request_status: str | None = None
        request_id: UUID | None = None
        approval_mode: str | None = None
        if principal_request is not None:
            request_id = principal_request.id
            approval_mode = principal_request.approval_mode
            acquisition_for_request: Acquisition | None = acquisition
            if (
                principal_request.acquisition_id is not None
                and (acquisition is None or acquisition.id != principal_request.acquisition_id)
            ):
                acquisition_for_request = await AcquisitionRequestRepository(
                    database_session
                ).acquisition(principal_request.acquisition_id)
            request_status = _acquisition_request_status(
                principal_request.cancelled_at is not None,
                principal_request.approved_at is not None,
                asset.stored_object_id is not None,
                acquisition_for_request,
            )

        return AssetStatusResponse(
            asset_id=asset.id,
            format=asset.format,
            media_type=asset.media_type,
            byte_size=asset.byte_size,
            stored=asset.stored_object_id is not None,
            acquisition_id=acquisition.id if acquisition is not None else None,
            acquisition_status=acquisition.status if acquisition is not None else None,
            acquisition_request_id=request_id,
            acquisition_request_status=request_status,
            acquisition_approval_mode=approval_mode,
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
        resume_repository: LibraryResumeRepository,
        entry: LibraryEntry,
    ) -> LibraryItemResponse:
        work = await repository.get_work(entry.work_id)
        if work is None:
            raise RuntimeError(f"Library entry {entry.id} references missing work")

        document_id = None
        readable_format = None
        progress_fraction = None
        reading_status = None
        resume = await resume_repository.latest_for_entry(entry)
        if resume is not None:
            document_id = resume.document.id
            readable_format = resume.asset.format
            progress_fraction = resume.state.progress_fraction
            reading_status = resume.state.status
        else:
            readable = await repository.readable_document_for_entry(entry)
            if readable is not None:
                document, asset = readable
                document_id = document.id
                readable_format = asset.format

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


def _normalized_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def _effective_reading_status(item: LibraryItemResponse) -> str:
    return item.reading_status or LibraryReadingStatus.UNREAD.value


def _reading_status(value: str | None) -> LibraryReadingStatus | None:
    return LibraryReadingStatus(value) if value is not None else None


def _reading_status_value(value: LibraryReadingStatus | None) -> str | None:
    return value.value if value is not None else None


def _smart_shelf_response(shelf: LibrarySmartShelf, item_count: int) -> SmartShelfResponse:
    return SmartShelfResponse(
        smart_shelf_id=shelf.id,
        name=shelf.name,
        description=shelf.description,
        rule=SmartShelfRule(
            reading_status=_reading_status(shelf.reading_status),
            collection_id=shelf.collection_id,
            tag_id=shelf.tag_id,
        ),
        item_count=item_count,
    )


def _acquisition_request_status(
    cancelled: bool,
    approved: bool,
    stored: bool,
    acquisition: Acquisition | None,
) -> str:
    if cancelled:
        return AcquisitionRequestStatus.CANCELLED.value
    acquisition_status = (
        AcquisitionStatus(acquisition.status) if acquisition is not None else None
    )
    if stored or acquisition_status is AcquisitionStatus.STORED:
        return AcquisitionRequestStatus.STORED.value
    if acquisition_status is AcquisitionStatus.QUARANTINED:
        return AcquisitionRequestStatus.QUARANTINED.value
    if acquisition_status is AcquisitionStatus.FAILED:
        return AcquisitionRequestStatus.FAILED.value
    if approved:
        return AcquisitionRequestStatus.ACTIVE.value
    return AcquisitionRequestStatus.PENDING_APPROVAL.value


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
    "LibraryOrganizationConflict",
    "LibraryOrganizationNotFound",
    "LibraryService",
    "LibraryTargetNotFound",
]
