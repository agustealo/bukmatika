from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableAssetManifest,
    PortableBookmark,
    PortableBytePolicy,
    PortableCollection,
    PortableDocumentIdentity,
    PortableEditionIdentity,
    PortableHighlight,
    PortableIdentifier,
    PortableLibraryEntry,
    PortableReadingPosition,
    PortableReadingState,
    PortableRightsEvidence,
    PortableRightsSnapshot,
    PortableSmartShelf,
    PortableSourceReference,
    PortableTag,
    PortableWorkIdentity,
    ReadingStatus,
)
from bukmatika.persistence import session_scope
from bukmatika.persistence.library_organization import LibraryOrganizationRepository
from bukmatika.persistence.library_portability import (
    AssetPortabilityRecord,
    LibraryPortabilityRepository,
    ReadingStatePortabilityRecord,
)
from bukmatika.persistence.models import Edition, Work

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _reading_status(value: str) -> ReadingStatus:
    if value == "unread":
        return "unread"
    if value == "reading":
        return "reading"
    if value == "finished":
        return "finished"
    raise ValueError(f"invalid persisted reading status: {value!r}")


def _optional_reading_status(value: str | None) -> ReadingStatus | None:
    return None if value is None else _reading_status(value)


class LibraryPortabilityService:
    """Create a byte-free, principal-owned portability manifest from canonical state."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def export(self, *, principal_id: UUID) -> LibraryPortabilityExportResponse:
        async with self._session_scope() as database_session:
            repository = LibraryPortabilityRepository(database_session)
            organization = LibraryOrganizationRepository(database_session)
            entries = await repository.entries(principal_id)
            entry_ids = [entry.id for entry in entries]
            collections_by_entry, tags_by_entry = await organization.organization_for_entries(
                principal_id=principal_id,
                entry_ids=entry_ids,
            )
            collection_rows = await organization.collections(principal_id)
            tag_rows = await organization.tags(principal_id)
            smart_shelves = await organization.smart_shelves(principal_id)

            portable_entries: list[PortableLibraryEntry] = []
            for entry in entries:
                work = await repository.work(entry.work_id)
                edition = (
                    await repository.edition(entry.edition_id)
                    if entry.edition_id is not None
                    else None
                )
                asset_records = await repository.asset_records(entry)
                reading_records = await repository.reading_states(
                    principal_id=principal_id,
                    library_entry_id=entry.id,
                )
                portable_entries.append(
                    PortableLibraryEntry(
                        source_library_entry_id=entry.id,
                        status=entry.status,
                        created_at=entry.created_at,
                        updated_at=entry.updated_at,
                        work=await self._work_identity(repository, work),
                        edition=(
                            await self._edition_identity(repository, edition)
                            if edition is not None
                            else None
                        ),
                        assets=[
                            await self._asset_manifest(repository, record)
                            for record in asset_records
                        ],
                        reading_states=[
                            self._reading_state(record) for record in reading_records
                        ],
                        collection_ids=[
                            collection.id
                            for collection in collections_by_entry.get(entry.id, [])
                        ],
                        tag_ids=[tag.id for tag in tags_by_entry.get(entry.id, [])],
                    )
                )

            return LibraryPortabilityExportResponse(
                exported_at=datetime.now(UTC),
                entries=portable_entries,
                collections=[
                    PortableCollection(
                        source_collection_id=collection.id,
                        name=collection.name,
                        description=collection.description,
                        created_at=collection.created_at,
                        updated_at=collection.updated_at,
                    )
                    for collection, _ in collection_rows
                ],
                tags=[
                    PortableTag(
                        source_tag_id=tag.id,
                        name=tag.name,
                        created_at=tag.created_at,
                        updated_at=tag.updated_at,
                    )
                    for tag, _ in tag_rows
                ],
                smart_shelves=[
                    PortableSmartShelf(
                        source_smart_shelf_id=shelf.id,
                        name=shelf.name,
                        description=shelf.description,
                        reading_status=_optional_reading_status(shelf.reading_status),
                        collection_id=shelf.collection_id,
                        tag_id=shelf.tag_id,
                        created_at=shelf.created_at,
                        updated_at=shelf.updated_at,
                    )
                    for shelf in smart_shelves
                ],
            )

    async def _work_identity(
        self,
        repository: LibraryPortabilityRepository,
        work: Work,
    ) -> PortableWorkIdentity:
        identifiers = await repository.identifiers("work", work.id)
        sources = await repository.source_references("work", work.id)
        return PortableWorkIdentity(
            source_work_id=work.id,
            canonical_title=work.canonical_title,
            authors=await repository.authors(work.id),
            subjects=await repository.subjects(work.id),
            identifiers=[
                PortableIdentifier(scheme=item.scheme, value=item.value) for item in identifiers
            ],
            sources=[
                PortableSourceReference(
                    provider=item.source.provider,
                    provider_record_id=item.source.provider_record_id,
                    canonical_url=item.source.canonical_url,
                    relationship=item.relationship,
                )
                for item in sources
            ],
        )

    async def _edition_identity(
        self,
        repository: LibraryPortabilityRepository,
        edition: Edition,
    ) -> PortableEditionIdentity:
        identifiers = await repository.identifiers("edition", edition.id)
        sources = await repository.source_references("edition", edition.id)
        return PortableEditionIdentity(
            source_edition_id=edition.id,
            title=edition.title,
            language=edition.language,
            publication_year=edition.publication_year,
            publisher=edition.publisher,
            edition_statement=edition.edition_statement,
            identifiers=[
                PortableIdentifier(scheme=item.scheme, value=item.value) for item in identifiers
            ],
            sources=[
                PortableSourceReference(
                    provider=item.source.provider,
                    provider_record_id=item.source.provider_record_id,
                    canonical_url=item.source.canonical_url,
                    relationship=item.relationship,
                )
                for item in sources
            ],
        )

    async def _asset_manifest(
        self,
        repository: LibraryPortabilityRepository,
        record: AssetPortabilityRecord,
    ) -> PortableAssetManifest:
        asset = record.asset
        identifiers = await repository.identifiers("asset", asset.id)
        sources = await repository.source_references("asset", asset.id)
        rights = record.rights_decision
        rights_snapshot = (
            PortableRightsSnapshot(
                rights_state=rights.rights_state,
                jurisdiction=rights.jurisdiction,
                policy_version=rights.policy_version,
                permissions=dict(rights.permissions),
                reason=rights.reason,
                evaluated_at=rights.evaluated_at,
                evidence=[
                    PortableRightsEvidence(
                        state=evidence.state,
                        source=evidence.source,
                        basis=evidence.basis,
                        evidence_url=evidence.evidence_url,
                        license_uri=evidence.license_uri,
                        confidence=evidence.confidence,
                    )
                    for evidence in record.rights_evidence
                ],
            )
            if rights is not None
            else None
        )
        content_sha256 = (
            record.document.source_sha256
            if record.document is not None
            else record.stored_object.sha256
            if record.stored_object is not None
            else None
        )
        byte_size = (
            record.stored_object.byte_size
            if record.stored_object is not None
            else asset.byte_size
        )
        return PortableAssetManifest(
            source_asset_id=asset.id,
            edition=await self._edition_identity(repository, record.edition),
            format=asset.format,
            media_type=asset.media_type,
            byte_size=byte_size,
            content_sha256=content_sha256,
            identifiers=[
                PortableIdentifier(scheme=item.scheme, value=item.value) for item in identifiers
            ],
            sources=[
                PortableSourceReference(
                    provider=item.source.provider,
                    provider_record_id=item.source.provider_record_id,
                    canonical_url=item.source.canonical_url,
                    relationship=item.relationship,
                )
                for item in sources
            ],
            document=(
                PortableDocumentIdentity(
                    source_document_id=record.document.id,
                    source_sha256=record.document.source_sha256,
                    format=record.document.format,
                    parser_name=record.document.parser_name,
                    parser_version=record.document.parser_version,
                )
                if record.document is not None
                else None
            ),
            rights=rights_snapshot,
            byte_policy=PortableBytePolicy(
                policy_export_allowed=(
                    bool(rights.permissions.get("export", False)) if rights is not None else False
                ),
                policy_share_allowed=(
                    bool(rights.permissions.get("share", False)) if rights is not None else False
                ),
            ),
        )

    @staticmethod
    def _reading_state(record: ReadingStatePortabilityRecord) -> PortableReadingState:
        state = record.state
        position = (
            PortableReadingPosition(
                section_ordinal=state.section_ordinal,
                char_offset=state.char_offset,
                locator=state.locator,
            )
            if state.section_ordinal is not None
            else None
        )
        return PortableReadingState(
            source_reading_state_id=state.id,
            document=PortableDocumentIdentity(
                source_document_id=record.document.id,
                source_sha256=record.document.source_sha256,
                format=record.document.format,
                parser_name=record.document.parser_name,
                parser_version=record.document.parser_version,
            ),
            status=_reading_status(state.status),
            progress_fraction=state.progress_fraction,
            position=position,
            last_read_at=state.last_read_at,
            created_at=state.created_at,
            updated_at=state.updated_at,
            bookmarks=[
                PortableBookmark(
                    source_bookmark_id=item.bookmark.id,
                    section_ordinal=item.section_ordinal,
                    char_offset=item.bookmark.char_offset,
                    locator=item.bookmark.locator,
                    label=item.bookmark.label,
                    created_at=item.bookmark.created_at,
                    updated_at=item.bookmark.updated_at,
                )
                for item in record.bookmarks
            ],
            highlights=[
                PortableHighlight(
                    source_highlight_id=item.highlight.id,
                    section_ordinal=item.section_ordinal,
                    char_start=item.highlight.char_start,
                    char_end=item.highlight.char_end,
                    locator=item.highlight.locator,
                    note=item.highlight.note,
                    created_at=item.highlight.created_at,
                    updated_at=item.highlight.updated_at,
                )
                for item in record.highlights
            ],
        )
