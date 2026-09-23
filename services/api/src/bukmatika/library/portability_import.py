from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.library.portability_domain import (
    ImportPlanTarget,
    LibraryPortabilityExportResponse,
    LibraryPortabilityImportPlanResponse,
    PortableEditionIdentity,
    PortableImportConflict,
    PortableImportEntryPlan,
    PortableImportTargetPlan,
    PortableReadingState,
    PortableSmartShelf,
    PortableWorkIdentity,
)
from bukmatika.normalization import normalize_identifier, normalize_text
from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import Document, DocumentSection
from bukmatika.persistence.library_portability_import import LibraryImportPlanningRepository
from bukmatika.persistence.models import Edition

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class LibraryPortabilityImportPlanner:
    """Plan a deterministic byte-free import without mutating canonical state."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def plan(
        self,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
    ) -> LibraryPortabilityImportPlanResponse:
        async with self._session_scope() as database_session:
            repository = LibraryImportPlanningRepository(database_session)
            conflicts: list[PortableImportConflict] = []

            collection_plans = await self._organization_plans(
                repository,
                principal_id=principal_id,
                target="collection",
                values=[(item.source_collection_id, item.name) for item in manifest.collections],
            )
            tag_plans = await self._organization_plans(
                repository,
                principal_id=principal_id,
                target="tag",
                values=[(item.source_tag_id, item.name) for item in manifest.tags],
            )
            collection_by_source = {item.source_id: item for item in collection_plans}
            tag_by_source = {item.source_id: item for item in tag_plans}

            entry_plans: list[PortableImportEntryPlan] = []
            for entry in manifest.entries:
                work_plan, work_id = await self._work_plan(repository, entry.work, conflicts)
                edition_plan, edition_id = await self._edition_plan(
                    repository,
                    work_plan=work_plan,
                    work_id=work_id,
                    edition=entry.edition,
                    conflicts=conflicts,
                )
                library_entry_plan = await self._library_entry_plan(
                    repository,
                    principal_id=principal_id,
                    source_id=entry.source_library_entry_id,
                    work_plan=work_plan,
                    work_id=work_id,
                    edition_plan=edition_plan,
                    edition_id=edition_id,
                    conflicts=conflicts,
                )

                document_plans: list[PortableImportTargetPlan] = []
                reading_plans: list[PortableImportTargetPlan] = []
                for reading in entry.reading_states:
                    document_plan, document = await self._document_plan(
                        repository,
                        work_plan=work_plan,
                        work_id=work_id,
                        edition_plan=edition_plan,
                        edition_id=edition_id,
                        reading=reading,
                        conflicts=conflicts,
                    )
                    document_plans.append(document_plan)
                    reading_plans.append(
                        await self._reading_plan(
                            repository,
                            library_entry_plan=library_entry_plan,
                            document_plan=document_plan,
                            document=document,
                            reading=reading,
                            conflicts=conflicts,
                        )
                    )

                entry_plans.append(
                    PortableImportEntryPlan(
                        source_library_entry_id=entry.source_library_entry_id,
                        work=work_plan,
                        edition=edition_plan,
                        library_entry=library_entry_plan,
                        documents=document_plans,
                        reading_states=reading_plans,
                        collection_ids=entry.collection_ids,
                        tag_ids=entry.tag_ids,
                    )
                )

            smart_shelf_plans = [
                await self._smart_shelf_plan(
                    repository,
                    principal_id=principal_id,
                    shelf=shelf,
                    collection_by_source=collection_by_source,
                    tag_by_source=tag_by_source,
                    conflicts=conflicts,
                )
                for shelf in manifest.smart_shelves
            ]

            self._validate_manifest_references(
                manifest=manifest,
                collection_by_source=collection_by_source,
                tag_by_source=tag_by_source,
                conflicts=conflicts,
            )

            return LibraryPortabilityImportPlanResponse(
                can_apply=not conflicts,
                entries=entry_plans,
                collections=collection_plans,
                tags=tag_plans,
                smart_shelves=smart_shelf_plans,
                conflicts=conflicts,
            )

    async def _work_plan(
        self,
        repository: LibraryImportPlanningRepository,
        work: PortableWorkIdentity,
        conflicts: list[PortableImportConflict],
    ) -> tuple[PortableImportTargetPlan, UUID | None]:
        candidates: set[UUID] = set()
        for identifier in work.identifiers:
            candidates.update(
                await repository.work_ids_for_identifier(
                    identifier.scheme,
                    normalize_identifier(identifier.value),
                )
            )
        candidates.update(
            await repository.work_ids_for_sources(
                [(source.provider, source.provider_record_id) for source in work.sources]
            )
        )
        if len(candidates) > 1:
            return self._conflict_plan(
                target="work",
                source_id=work.source_work_id,
                code="work_identity_conflict",
                detail="Portable work evidence resolves to multiple canonical works",
                conflicts=conflicts,
            )
        if len(candidates) == 1:
            destination = next(iter(candidates))
            return (
                PortableImportTargetPlan(
                    target="work",
                    source_id=work.source_work_id,
                    action="match",
                    destination_id=destination,
                    reason="Matched durable identifier or provider source evidence",
                ),
                destination,
            )

        fallback = await repository.work_ids_for_metadata(
            normalized_title=normalize_text(work.canonical_title),
            normalized_author=(normalize_text(work.authors[0]) if work.authors else None),
        )
        if len(fallback) > 1:
            return self._conflict_plan(
                target="work",
                source_id=work.source_work_id,
                code="work_metadata_ambiguous",
                detail="Portable work metadata matches multiple canonical works",
                conflicts=conflicts,
            )
        if len(fallback) == 1:
            destination = next(iter(fallback))
            return (
                PortableImportTargetPlan(
                    target="work",
                    source_id=work.source_work_id,
                    action="match",
                    destination_id=destination,
                    reason="Matched unambiguous normalized title/author metadata",
                ),
                destination,
            )
        return (
            PortableImportTargetPlan(
                target="work",
                source_id=work.source_work_id,
                action="create",
                reason="No canonical work matches durable identity or unambiguous metadata",
            ),
            None,
        )

    async def _edition_plan(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        work_plan: PortableImportTargetPlan,
        work_id: UUID | None,
        edition: PortableEditionIdentity | None,
        conflicts: list[PortableImportConflict],
    ) -> tuple[PortableImportTargetPlan | None, UUID | None]:
        if edition is None:
            return None, None
        if work_plan.action == "conflict":
            return self._conflict_plan(
                target="edition",
                source_id=edition.source_edition_id,
                code="edition_work_unresolved",
                detail="Edition cannot resolve because its work identity is conflicted",
                conflicts=conflicts,
            )
        if work_id is None:
            return (
                PortableImportTargetPlan(
                    target="edition",
                    source_id=edition.source_edition_id,
                    action="create",
                    reason="Edition will be created under the planned new canonical work",
                ),
                None,
            )

        candidates: set[UUID] = set()
        for identifier in edition.identifiers:
            candidates.update(
                await repository.edition_ids_for_identifier(
                    identifier.scheme,
                    normalize_identifier(identifier.value),
                )
            )
        candidates.update(
            await repository.edition_ids_for_sources(
                [(source.provider, source.provider_record_id) for source in edition.sources]
            )
        )
        if len(candidates) > 1:
            return self._conflict_plan(
                target="edition",
                source_id=edition.source_edition_id,
                code="edition_identity_conflict",
                detail="Portable edition evidence resolves to multiple canonical editions",
                conflicts=conflicts,
            )
        if len(candidates) == 1:
            destination = next(iter(candidates))
            if await repository.edition_work_id(destination) != work_id:
                return self._conflict_plan(
                    target="edition",
                    source_id=edition.source_edition_id,
                    code="edition_work_conflict",
                    detail="Matched edition belongs to a different canonical work",
                    conflicts=conflicts,
                )
            return (
                PortableImportTargetPlan(
                    target="edition",
                    source_id=edition.source_edition_id,
                    action="match",
                    destination_id=destination,
                    reason="Matched durable identifier or provider source evidence",
                ),
                destination,
            )

        metadata_matches = [
            candidate
            for candidate in await repository.editions_for_work(work_id)
            if self._edition_metadata_matches(candidate, edition)
        ]
        if len(metadata_matches) > 1:
            return self._conflict_plan(
                target="edition",
                source_id=edition.source_edition_id,
                code="edition_metadata_ambiguous",
                detail="Portable edition metadata matches multiple editions under the work",
                conflicts=conflicts,
            )
        if len(metadata_matches) == 1:
            destination = metadata_matches[0].id
            return (
                PortableImportTargetPlan(
                    target="edition",
                    source_id=edition.source_edition_id,
                    action="match",
                    destination_id=destination,
                    reason="Matched unambiguous edition metadata within the canonical work",
                ),
                destination,
            )
        return (
            PortableImportTargetPlan(
                target="edition",
                source_id=edition.source_edition_id,
                action="create",
                reason="No canonical edition matches durable identity or unambiguous metadata",
            ),
            None,
        )

    async def _library_entry_plan(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        principal_id: UUID,
        source_id: UUID,
        work_plan: PortableImportTargetPlan,
        work_id: UUID | None,
        edition_plan: PortableImportTargetPlan | None,
        edition_id: UUID | None,
        conflicts: list[PortableImportConflict],
    ) -> PortableImportTargetPlan:
        if work_plan.action == "conflict" or (
            edition_plan is not None and edition_plan.action == "conflict"
        ):
            plan, _ = self._conflict_plan(
                target="library_entry",
                source_id=source_id,
                code="library_entry_identity_conflict",
                detail="Library entry cannot resolve while its canonical identity is conflicted",
                conflicts=conflicts,
            )
            return plan
        if work_id is None or (edition_plan is not None and edition_id is None):
            return PortableImportTargetPlan(
                target="library_entry",
                source_id=source_id,
                action="create",
                reason="Library entry will be created after its planned canonical identity",
            )
        existing = await repository.library_entry_id(
            principal_id=principal_id,
            work_id=work_id,
            edition_id=edition_id,
        )
        if existing is not None:
            return PortableImportTargetPlan(
                target="library_entry",
                source_id=source_id,
                action="match",
                destination_id=existing,
                reason="Matched existing principal-owned library entry",
            )
        return PortableImportTargetPlan(
            target="library_entry",
            source_id=source_id,
            action="create",
            reason="Canonical identity exists but is not yet in this principal's library",
        )

    async def _document_plan(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        work_plan: PortableImportTargetPlan,
        work_id: UUID | None,
        edition_plan: PortableImportTargetPlan | None,
        edition_id: UUID | None,
        reading: PortableReadingState,
        conflicts: list[PortableImportConflict],
    ) -> tuple[PortableImportTargetPlan, Document | None]:
        source = reading.document
        if work_plan.action == "conflict" or (
            edition_plan is not None and edition_plan.action == "conflict"
        ):
            return self._conflict_plan(
                target="document",
                source_id=source.source_document_id,
                code="document_identity_conflict",
                detail="Document cannot resolve while its work/edition identity is conflicted",
                conflicts=conflicts,
            )
        if work_id is None or (edition_plan is not None and edition_id is None):
            return (
                PortableImportTargetPlan(
                    target="document",
                    source_id=source.source_document_id,
                    action="skip",
                    reason=(
                        "No compatible local canonical document exists "
                        "for a planned new identity"
                    ),
                ),
                None,
            )
        candidates = await repository.documents_for_sha(
            source_sha256=source.source_sha256,
            work_id=work_id,
            edition_id=edition_id,
        )
        if not candidates:
            return (
                PortableImportTargetPlan(
                    target="document",
                    source_id=source.source_document_id,
                    action="skip",
                    reason=(
                        "Book bytes/document are not present locally; "
                        "metadata import remains valid"
                    ),
                ),
                None,
            )
        exact = [
            document
            for document in candidates
            if document.format.casefold() == source.format.casefold()
            and document.parser_name == source.parser_name
            and document.parser_version == source.parser_version
        ]
        if not exact:
            return self._conflict_plan(
                target="document",
                source_id=source.source_document_id,
                code="document_parser_incompatible",
                detail="Content SHA matches locally, but parser/format identity is incompatible",
                conflicts=conflicts,
            )
        if len(exact) > 1:
            return self._conflict_plan(
                target="document",
                source_id=source.source_document_id,
                code="document_identity_ambiguous",
                detail="Multiple local documents match the portable content/parser identity",
                conflicts=conflicts,
            )
        document = exact[0]
        return (
            PortableImportTargetPlan(
                target="document",
                source_id=source.source_document_id,
                action="match",
                destination_id=document.id,
                reason="Matched content SHA-256 and compatible canonical parser identity",
            ),
            document,
        )

    async def _reading_plan(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        library_entry_plan: PortableImportTargetPlan,
        document_plan: PortableImportTargetPlan,
        document: Document | None,
        reading: PortableReadingState,
        conflicts: list[PortableImportConflict],
    ) -> PortableImportTargetPlan:
        if document_plan.action == "skip":
            return PortableImportTargetPlan(
                target="reading_state",
                source_id=reading.source_reading_state_id,
                action="skip",
                reason="Reader state waits until its exact local document is available",
            )
        if document_plan.action == "conflict" or document is None:
            plan, _ = self._conflict_plan(
                target="reading_state",
                source_id=reading.source_reading_state_id,
                code="reading_document_conflict",
                detail="Reader state cannot apply because its document identity is conflicted",
                conflicts=conflicts,
            )
            return plan

        coordinate_conflicts = await self._coordinate_conflicts(
            repository,
            document=document,
            reading=reading,
        )
        if coordinate_conflicts:
            detail = "; ".join(coordinate_conflicts)
            plan, _ = self._conflict_plan(
                target="reading_state",
                source_id=reading.source_reading_state_id,
                code="reading_coordinates_incompatible",
                detail=detail,
                conflicts=conflicts,
            )
            return plan

        existing = None
        if library_entry_plan.destination_id is not None:
            existing = await repository.reading_state(
                library_entry_id=library_entry_plan.destination_id,
                document_id=document.id,
            )
        if existing is not None and existing.updated_at > reading.updated_at:
            plan, _ = self._conflict_plan(
                target="reading_state",
                source_id=reading.source_reading_state_id,
                code="local_reader_state_newer",
                detail="Local reader state is newer and will not be silently overwritten",
                conflicts=conflicts,
            )
            return plan
        return PortableImportTargetPlan(
            target="reading_state",
            source_id=reading.source_reading_state_id,
            action="apply",
            destination_id=existing.id if existing is not None else None,
            reason="Exact document coordinates are compatible and local state is not newer",
        )

    async def _organization_plans(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        principal_id: UUID,
        target: ImportPlanTarget,
        values: list[tuple[UUID, str]],
    ) -> list[PortableImportTargetPlan]:
        plans: list[PortableImportTargetPlan] = []
        for source_id, name in values:
            normalized_name = normalize_text(name)
            existing_id: UUID | None
            if target == "collection":
                collection = await repository.collection_by_normalized_name(
                    principal_id=principal_id,
                    normalized_name=normalized_name,
                )
                existing_id = collection.id if collection is not None else None
            elif target == "tag":
                tag = await repository.tag_by_normalized_name(
                    principal_id=principal_id,
                    normalized_name=normalized_name,
                )
                existing_id = tag.id if tag is not None else None
            else:
                raise ValueError(f"Unsupported organization target: {target}")
            plans.append(
                PortableImportTargetPlan(
                    target=target,
                    source_id=source_id,
                    action="match" if existing_id is not None else "create",
                    destination_id=existing_id,
                    reason=(
                        "Matched principal-owned normalized name"
                        if existing_id is not None
                        else "No principal-owned normalized-name match"
                    ),
                )
            )
        return plans

    async def _smart_shelf_plan(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        principal_id: UUID,
        shelf: PortableSmartShelf,
        collection_by_source: dict[UUID, PortableImportTargetPlan],
        tag_by_source: dict[UUID, PortableImportTargetPlan],
        conflicts: list[PortableImportConflict],
    ) -> PortableImportTargetPlan:
        collection_plan = (
            collection_by_source.get(shelf.collection_id)
            if shelf.collection_id is not None
            else None
        )
        tag_plan = tag_by_source.get(shelf.tag_id) if shelf.tag_id is not None else None
        if shelf.collection_id is not None and collection_plan is None:
            return self._conflict_plan(
                target="smart_shelf",
                source_id=shelf.source_smart_shelf_id,
                code="smart_shelf_collection_missing",
                detail="Smart shelf references a collection absent from the manifest",
                conflicts=conflicts,
            )[0]
        if shelf.tag_id is not None and tag_plan is None:
            return self._conflict_plan(
                target="smart_shelf",
                source_id=shelf.source_smart_shelf_id,
                code="smart_shelf_tag_missing",
                detail="Smart shelf references a tag absent from the manifest",
                conflicts=conflicts,
            )[0]

        existing = await repository.smart_shelf_by_normalized_name(
            principal_id=principal_id,
            normalized_name=normalize_text(shelf.name),
        )
        if existing is None:
            return PortableImportTargetPlan(
                target="smart_shelf",
                source_id=shelf.source_smart_shelf_id,
                action="create",
                reason="No principal-owned normalized-name match",
            )

        expected_collection = (
            collection_plan.destination_id if collection_plan is not None else None
        )
        expected_tag = tag_plan.destination_id if tag_plan is not None else None
        unresolved_reference = (
            collection_plan is not None and collection_plan.action == "create"
        ) or (tag_plan is not None and tag_plan.action == "create")
        if unresolved_reference or (
            existing.reading_status != shelf.reading_status
            or existing.collection_id != expected_collection
            or existing.tag_id != expected_tag
        ):
            return self._conflict_plan(
                target="smart_shelf",
                source_id=shelf.source_smart_shelf_id,
                code="smart_shelf_rule_conflict",
                detail="Existing smart shelf name has a different or not-yet-resolvable rule",
                conflicts=conflicts,
            )[0]
        return PortableImportTargetPlan(
            target="smart_shelf",
            source_id=shelf.source_smart_shelf_id,
            action="match",
            destination_id=existing.id,
            reason="Matched principal-owned name and identical resolved rule",
        )

    async def _coordinate_conflicts(
        self,
        repository: LibraryImportPlanningRepository,
        *,
        document: Document,
        reading: PortableReadingState,
    ) -> list[str]:
        checks: dict[int, list[tuple[str, Mapping[str, object], int, int | None]]] = {}
        if reading.position is not None:
            checks.setdefault(reading.position.section_ordinal, []).append(
                (
                    "position",
                    reading.position.locator,
                    reading.position.char_offset or 0,
                    None,
                )
            )
        for bookmark in reading.bookmarks:
            checks.setdefault(bookmark.section_ordinal, []).append(
                ("bookmark", bookmark.locator, bookmark.char_offset, None)
            )
        for highlight in reading.highlights:
            checks.setdefault(highlight.section_ordinal, []).append(
                ("highlight", highlight.locator, highlight.char_start, highlight.char_end)
            )

        problems: list[str] = []
        for ordinal, coordinate_checks in checks.items():
            section = await repository.section_by_ordinal(
                document_id=document.id,
                ordinal=ordinal,
            )
            if section is None:
                problems.append(f"section {ordinal} is absent locally")
                continue
            problems.extend(self._section_coordinate_problems(section, coordinate_checks))
        return problems

    @staticmethod
    def _section_coordinate_problems(
        section: DocumentSection,
        coordinate_checks: list[tuple[str, Mapping[str, object], int, int | None]],
    ) -> list[str]:
        problems: list[str] = []
        for kind, locator, start, end in coordinate_checks:
            if dict(section.locator) != dict(locator):
                problems.append(f"{kind} locator differs at section {section.ordinal}")
                continue
            if start < 0 or start > len(section.text):
                problems.append(f"{kind} offset is outside section {section.ordinal}")
            if end is not None and (end <= start or end > len(section.text)):
                problems.append(f"{kind} range is outside section {section.ordinal}")
        return problems

    @staticmethod
    def _edition_metadata_matches(
        candidate: Edition,
        portable: PortableEditionIdentity,
    ) -> bool:
        return (
            normalize_text(candidate.title) == normalize_text(portable.title)
            and (
                portable.language is None
                or normalize_text(candidate.language or "") == normalize_text(portable.language)
            )
            and (
                portable.publication_year is None
                or candidate.publication_year == portable.publication_year
            )
            and (
                portable.publisher is None
                or normalize_text(candidate.publisher or "") == normalize_text(portable.publisher)
            )
            and (
                portable.edition_statement is None
                or normalize_text(candidate.edition_statement or "")
                == normalize_text(portable.edition_statement)
            )
        )

    @staticmethod
    def _conflict_plan(
        *,
        target: ImportPlanTarget,
        source_id: UUID,
        code: str,
        detail: str,
        conflicts: list[PortableImportConflict],
    ) -> tuple[PortableImportTargetPlan, None]:
        conflicts.append(
            PortableImportConflict(
                target=target,
                source_id=source_id,
                code=code,
                detail=detail,
            )
        )
        return (
            PortableImportTargetPlan(
                target=target,
                source_id=source_id,
                action="conflict",
                reason=detail,
            ),
            None,
        )

    @staticmethod
    def _validate_manifest_references(
        *,
        manifest: LibraryPortabilityExportResponse,
        collection_by_source: dict[UUID, PortableImportTargetPlan],
        tag_by_source: dict[UUID, PortableImportTargetPlan],
        conflicts: list[PortableImportConflict],
    ) -> None:
        for entry in manifest.entries:
            for collection_id in entry.collection_ids:
                if collection_id not in collection_by_source:
                    conflicts.append(
                        PortableImportConflict(
                            target="library_entry",
                            source_id=entry.source_library_entry_id,
                            code="entry_collection_missing",
                            detail=f"Entry references missing collection {collection_id}",
                        )
                    )
            for tag_id in entry.tag_ids:
                if tag_id not in tag_by_source:
                    conflicts.append(
                        PortableImportConflict(
                            target="library_entry",
                            source_id=entry.source_library_entry_id,
                            code="entry_tag_missing",
                            detail=f"Entry references missing tag {tag_id}",
                        )
                    )