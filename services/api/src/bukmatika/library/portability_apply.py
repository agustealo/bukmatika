from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    LibraryPortabilityImportPlanResponse,
    PortableEditionIdentity,
    PortableImportConflict,
    PortableImportEntryPlan,
    PortableImportTargetPlan,
    PortableReadingState,
    PortableWorkIdentity,
)
from bukmatika.library.portability_import import LibraryPortabilityImportPlanner
from bukmatika.normalization import normalize_identifier, normalize_text
from bukmatika.persistence import session_scope
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.library import LibraryRepository
from bukmatika.persistence.library_organization import LibraryOrganizationRepository
from bukmatika.persistence.library_portability_apply import (
    LibraryImportApplyConflict,
    LibraryImportApplyRepository,
)
from bukmatika.persistence.readers import ReaderRepository

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class LibraryPortabilityImportApplySummary(BaseModel):
    works_created: int = 0
    editions_created: int = 0
    library_entries_created: int = 0
    collections_created: int = 0
    tags_created: int = 0
    smart_shelves_created: int = 0
    reading_states_applied: int = 0
    reading_states_skipped: int = 0


class LibraryPortabilityImportApplyResponse(BaseModel):
    schema_version: int = 1
    mode: str = "apply"
    committed: bool
    plan: LibraryPortabilityImportPlanResponse
    summary: LibraryPortabilityImportApplySummary


class LibraryPortabilityImportApplier:
    """Apply a manifest only after re-planning it inside the commit transaction."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def apply(
        self,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
    ) -> LibraryPortabilityImportApplyResponse:
        try:
            async with self._session_scope() as database_session:
                plan = await LibraryPortabilityImportPlanner(
                    session_scope_factory=self._bound_scope(database_session)
                ).plan(principal_id=principal_id, manifest=manifest)
                if not plan.can_apply:
                    return LibraryPortabilityImportApplyResponse(
                        committed=False,
                        plan=plan,
                        summary=LibraryPortabilityImportApplySummary(),
                    )
                summary = await self._apply_plan(
                    database_session,
                    principal_id=principal_id,
                    manifest=manifest,
                    plan=plan,
                )
                return LibraryPortabilityImportApplyResponse(
                    committed=True,
                    plan=plan,
                    summary=summary,
                )
        except LibraryImportApplyConflict as conflict:
            plan = await LibraryPortabilityImportPlanner(
                session_scope_factory=self._session_scope
            ).plan(principal_id=principal_id, manifest=manifest)
            duplicate = any(
                item.source_id == conflict.source_id and item.code == conflict.code
                for item in plan.conflicts
            )
            conflicts = list(plan.conflicts)
            if not duplicate:
                conflicts.append(
                    PortableImportConflict(
                        target="reading_state",
                        source_id=conflict.source_id,
                        code=conflict.code,
                        detail=conflict.detail,
                    )
                )
            failed_plan = plan.model_copy(
                update={
                    "can_apply": False,
                    "conflicts": conflicts,
                }
            )
            return LibraryPortabilityImportApplyResponse(
                committed=False,
                plan=failed_plan,
                summary=LibraryPortabilityImportApplySummary(),
            )

    async def _apply_plan(
        self,
        session: AsyncSession,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
        plan: LibraryPortabilityImportPlanResponse,
    ) -> LibraryPortabilityImportApplySummary:
        catalog = CatalogRepository(session)
        library = LibraryRepository(session)
        organization = LibraryOrganizationRepository(session)
        reader = ReaderRepository(session)
        apply_repository = LibraryImportApplyRepository(session)
        summary = LibraryPortabilityImportApplySummary()

        collection_ids: dict[UUID, UUID] = {}
        for portable, target in zip(manifest.collections, plan.collections, strict=True):
            if target.action == "match":
                collection_ids[portable.source_collection_id] = self._destination_id(target)
                continue
            created = await organization.create_collection(
                principal_id=principal_id,
                name=portable.name,
                normalized_name=normalize_text(portable.name),
                description=portable.description,
            )
            collection_ids[portable.source_collection_id] = created.id
            summary.collections_created += 1

        tag_ids: dict[UUID, UUID] = {}
        for portable, target in zip(manifest.tags, plan.tags, strict=True):
            if target.action == "match":
                tag_ids[portable.source_tag_id] = self._destination_id(target)
                continue
            created = await apply_repository.create_tag(
                principal_id=principal_id,
                name=portable.name,
                normalized_name=normalize_text(portable.name),
            )
            tag_ids[portable.source_tag_id] = created.id
            summary.tags_created += 1

        work_ids: dict[UUID, UUID] = {}
        edition_ids: dict[UUID, UUID] = {}
        for portable, entry_plan in zip(manifest.entries, plan.entries, strict=True):
            source_work_id = portable.work.source_work_id
            work_id = work_ids.get(source_work_id)
            if work_id is None:
                work_id = await self._resolve_work(
                    catalog,
                    apply_repository,
                    portable=portable.work,
                    target=entry_plan.work,
                    summary=summary,
                )
                work_ids[source_work_id] = work_id

            edition_id: UUID | None = None
            if portable.edition is not None:
                source_edition_id = portable.edition.source_edition_id
                edition_id = edition_ids.get(source_edition_id)
                if edition_id is None:
                    if entry_plan.edition is None:
                        raise RuntimeError("Planner omitted an edition target")
                    edition_id = await self._resolve_edition(
                        session,
                        catalog,
                        apply_repository,
                        work_id=work_id,
                        portable=portable.edition,
                        target=entry_plan.edition,
                        summary=summary,
                    )
                    edition_ids[source_edition_id] = edition_id

            if edition_id is None:
                library_entry = await library.save_work(principal_id, work_id)
            else:
                library_entry = await library.save_edition(principal_id, edition_id)
            if entry_plan.library_entry.action == "create":
                summary.library_entries_created += 1
            elif entry_plan.library_entry.action == "match":
                expected = self._destination_id(entry_plan.library_entry)
                if library_entry.id != expected:
                    raise RuntimeError("Planner/library entry resolution diverged during import")

            for source_collection_id in portable.collection_ids:
                await organization.add_collection_entry(
                    principal_id=principal_id,
                    collection_id=collection_ids[source_collection_id],
                    library_entry_id=library_entry.id,
                )
            for source_tag_id in portable.tag_ids:
                await apply_repository.add_tag_entry(
                    library_entry_id=library_entry.id,
                    tag_id=tag_ids[source_tag_id],
                )

            await self._apply_reading_states(
                apply_repository,
                reader,
                principal_id=principal_id,
                library_entry_id=library_entry.id,
                readings=portable.reading_states,
                entry_plan=entry_plan,
                summary=summary,
            )

        for portable, target in zip(manifest.smart_shelves, plan.smart_shelves, strict=True):
            if target.action == "match":
                continue
            await organization.create_smart_shelf(
                principal_id=principal_id,
                name=portable.name,
                normalized_name=normalize_text(portable.name),
                description=portable.description,
                reading_status=portable.reading_status,
                collection_id=(
                    collection_ids[portable.collection_id]
                    if portable.collection_id is not None
                    else None
                ),
                tag_id=tag_ids[portable.tag_id] if portable.tag_id is not None else None,
            )
            summary.smart_shelves_created += 1

        return summary

    async def _resolve_work(
        self,
        catalog: CatalogRepository,
        apply_repository: LibraryImportApplyRepository,
        *,
        portable: PortableWorkIdentity,
        target: PortableImportTargetPlan,
        summary: LibraryPortabilityImportApplySummary,
    ) -> UUID:
        if target.action == "match":
            work_id = self._destination_id(target)
        elif target.action == "create":
            work = await catalog.create_work(
                title=portable.canonical_title,
                normalized_title=normalize_text(portable.canonical_title),
            )
            work_id = work.id
            summary.works_created += 1
        else:
            raise RuntimeError(f"Unexpected work action during apply: {target.action}")

        for author in portable.authors:
            await catalog.add_author(
                work_id=work_id,
                display_name=author,
                normalized_name=normalize_text(author),
            )
        for subject in portable.subjects:
            await catalog.add_subject(
                work_id=work_id,
                display_name=subject,
                normalized_name=normalize_text(subject),
            )
        for identifier in portable.identifiers:
            await catalog.add_identifier(
                entity_type="work",
                entity_id=work_id,
                scheme=identifier.scheme,
                value=identifier.value,
                normalized_value=normalize_identifier(identifier.value),
            )
        for source in portable.sources:
            await apply_repository.ensure_source_reference(
                provider=source.provider,
                provider_record_id=source.provider_record_id,
                canonical_url=source.canonical_url,
                relationship=source.relationship,
                entity_type="work",
                entity_id=work_id,
            )
        return work_id

    async def _resolve_edition(
        self,
        session: AsyncSession,
        catalog: CatalogRepository,
        apply_repository: LibraryImportApplyRepository,
        *,
        work_id: UUID,
        portable: PortableEditionIdentity,
        target: PortableImportTargetPlan,
        summary: LibraryPortabilityImportApplySummary,
    ) -> UUID:
        if target.action == "match":
            edition_id = self._destination_id(target)
        elif target.action == "create":
            edition = await catalog.create_edition(
                work_id=work_id,
                title=portable.title,
                language=portable.language,
                publication_year=portable.publication_year,
                publisher=portable.publisher,
            )
            edition.edition_statement = portable.edition_statement
            await session.flush()
            edition_id = edition.id
            summary.editions_created += 1
        else:
            raise RuntimeError(f"Unexpected edition action during apply: {target.action}")

        for identifier in portable.identifiers:
            await catalog.add_identifier(
                entity_type="edition",
                entity_id=edition_id,
                scheme=identifier.scheme,
                value=identifier.value,
                normalized_value=normalize_identifier(identifier.value),
            )
        for source in portable.sources:
            await apply_repository.ensure_source_reference(
                provider=source.provider,
                provider_record_id=source.provider_record_id,
                canonical_url=source.canonical_url,
                relationship=source.relationship,
                entity_type="edition",
                entity_id=edition_id,
            )
        return edition_id

    async def _apply_reading_states(
        self,
        apply_repository: LibraryImportApplyRepository,
        reader: ReaderRepository,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        readings: list[PortableReadingState],
        entry_plan: PortableImportEntryPlan,
        summary: LibraryPortabilityImportApplySummary,
    ) -> None:
        for reading, document_plan, reading_plan in zip(
            readings,
            entry_plan.documents,
            entry_plan.reading_states,
            strict=True,
        ):
            if reading_plan.action == "skip":
                summary.reading_states_skipped += 1
                continue
            if reading_plan.action != "apply" or document_plan.action != "match":
                raise RuntimeError("Planner returned a non-applicable reader target in an applyable plan")
            document_id = self._destination_id(document_plan)
            await reader.require_access(principal_id, library_entry_id, document_id)

            section = None
            char_offset = None
            if reading.position is not None:
                section = await apply_repository.section_by_ordinal(
                    document_id=document_id,
                    ordinal=reading.position.section_ordinal,
                )
                char_offset = reading.position.char_offset
            state = await apply_repository.apply_reading_state(
                source_id=reading.source_reading_state_id,
                library_entry_id=library_entry_id,
                document_id=document_id,
                status=reading.status,
                progress_fraction=reading.progress_fraction,
                section=section,
                char_offset=char_offset,
                last_read_at=reading.last_read_at,
                created_at=reading.created_at,
                updated_at=reading.updated_at,
            )

            for bookmark in reading.bookmarks:
                bookmark_section = await apply_repository.section_by_ordinal(
                    document_id=document_id,
                    ordinal=bookmark.section_ordinal,
                )
                await apply_repository.apply_bookmark(
                    source_id=bookmark.source_bookmark_id,
                    reading_state_id=state.id,
                    section=bookmark_section,
                    char_offset=bookmark.char_offset,
                    label=bookmark.label,
                    created_at=bookmark.created_at,
                    updated_at=bookmark.updated_at,
                )
            for highlight in reading.highlights:
                highlight_section = await apply_repository.section_by_ordinal(
                    document_id=document_id,
                    ordinal=highlight.section_ordinal,
                )
                await apply_repository.apply_highlight(
                    source_id=highlight.source_highlight_id,
                    reading_state_id=state.id,
                    section=highlight_section,
                    char_start=highlight.char_start,
                    char_end=highlight.char_end,
                    note=highlight.note,
                    created_at=highlight.created_at,
                    updated_at=highlight.updated_at,
                )
            summary.reading_states_applied += 1

    @staticmethod
    def _destination_id(target: PortableImportTargetPlan) -> UUID:
        if target.destination_id is None:
            raise RuntimeError(f"{target.target} match is missing its destination id")
        return target.destination_id

    @staticmethod
    def _bound_scope(session: AsyncSession) -> SessionScopeFactory:
        @asynccontextmanager
        async def scope() -> AsyncIterator[AsyncSession]:
            yield session

        return scope
