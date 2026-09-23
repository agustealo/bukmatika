from collections.abc import Hashable
from uuid import UUID

from bukmatika.library.portability_domain import (
    ImportPlanTarget,
    LibraryPortabilityExportResponse,
    PortableDocumentIdentity,
    PortableEditionIdentity,
    PortableImportConflict,
    PortableLibraryEntry,
    PortableWorkIdentity,
)
from bukmatika.normalization import normalize_identifier, normalize_text


def validate_manifest_consistency(
    manifest: LibraryPortabilityExportResponse,
) -> list[PortableImportConflict]:
    """Reject self-contradictory portable identities before destination planning.

    Portable source UUIDs are references inside one manifest, not destination
    identity. Repeating a source UUID is only safe for embedded Work/Edition/
    Document identities when the repeated identity is semantically identical.
    Destination-unique records and durable identity evidence must not collide.
    """

    conflicts: list[PortableImportConflict] = []

    _reject_duplicate_records(
        [(item.source_collection_id, item.name) for item in manifest.collections],
        target="collection",
        code="manifest_duplicate_collection_source_id",
        detail="Collection source ids must be unique within one manifest",
        conflicts=conflicts,
    )
    _reject_normalized_name_collisions(
        [(item.source_collection_id, item.name) for item in manifest.collections],
        target="collection",
        code="manifest_collection_name_collision",
        conflicts=conflicts,
    )
    _reject_duplicate_records(
        [(item.source_tag_id, item.name) for item in manifest.tags],
        target="tag",
        code="manifest_duplicate_tag_source_id",
        detail="Tag source ids must be unique within one manifest",
        conflicts=conflicts,
    )
    _reject_normalized_name_collisions(
        [(item.source_tag_id, item.name) for item in manifest.tags],
        target="tag",
        code="manifest_tag_name_collision",
        conflicts=conflicts,
    )
    _reject_duplicate_records(
        [(item.source_smart_shelf_id, item.name) for item in manifest.smart_shelves],
        target="smart_shelf",
        code="manifest_duplicate_smart_shelf_source_id",
        detail="Smart-shelf source ids must be unique within one manifest",
        conflicts=conflicts,
    )
    _reject_normalized_name_collisions(
        [(item.source_smart_shelf_id, item.name) for item in manifest.smart_shelves],
        target="smart_shelf",
        code="manifest_smart_shelf_name_collision",
        conflicts=conflicts,
    )

    work_signatures: dict[UUID, Hashable] = {}
    edition_signatures: dict[UUID, Hashable] = {}
    document_signatures: dict[UUID, Hashable] = {}
    work_identifier_claims: dict[tuple[str, str], UUID] = {}
    work_source_claims: dict[tuple[str, str], UUID] = {}
    edition_identifier_claims: dict[tuple[str, str], UUID] = {}
    edition_source_claims: dict[tuple[str, str], UUID] = {}
    library_entry_ids: set[UUID] = set()
    logical_library_entries: dict[tuple[UUID, UUID | None], UUID] = {}
    asset_ids: set[UUID] = set()
    reading_state_ids: set[UUID] = set()
    bookmark_ids: set[UUID] = set()
    highlight_ids: set[UUID] = set()

    for entry in manifest.entries:
        if entry.source_library_entry_id in library_entry_ids:
            _append_conflict(
                conflicts,
                target="library_entry",
                source_id=entry.source_library_entry_id,
                code="manifest_duplicate_library_entry_source_id",
                detail="Library-entry source ids must be unique within one manifest",
            )
        library_entry_ids.add(entry.source_library_entry_id)

        _register_work(
            entry.work,
            signatures=work_signatures,
            identifier_claims=work_identifier_claims,
            source_claims=work_source_claims,
            conflicts=conflicts,
        )
        if entry.edition is not None:
            _register_edition(
                entry.edition,
                signatures=edition_signatures,
                identifier_claims=edition_identifier_claims,
                source_claims=edition_source_claims,
                conflicts=conflicts,
            )

        logical_entry = (
            entry.work.source_work_id,
            entry.edition.source_edition_id if entry.edition is not None else None,
        )
        previous_entry = logical_library_entries.get(logical_entry)
        if previous_entry is not None and previous_entry != entry.source_library_entry_id:
            _append_conflict(
                conflicts,
                target="library_entry",
                source_id=entry.source_library_entry_id,
                code="manifest_library_entry_identity_collision",
                detail=(
                    "Multiple portable library entries claim the same source Work/Edition identity"
                ),
            )
        else:
            logical_library_entries[logical_entry] = entry.source_library_entry_id

        _reject_duplicate_refs(entry, conflicts=conflicts)
        _validate_assets(
            entry,
            asset_ids=asset_ids,
            edition_signatures=edition_signatures,
            edition_identifier_claims=edition_identifier_claims,
            edition_source_claims=edition_source_claims,
            document_signatures=document_signatures,
            conflicts=conflicts,
        )
        _validate_reading_states(
            entry,
            reading_state_ids=reading_state_ids,
            bookmark_ids=bookmark_ids,
            highlight_ids=highlight_ids,
            document_signatures=document_signatures,
            conflicts=conflicts,
        )

    return conflicts


def _register_work(
    work: PortableWorkIdentity,
    *,
    signatures: dict[UUID, Hashable],
    identifier_claims: dict[tuple[str, str], UUID],
    source_claims: dict[tuple[str, str], UUID],
    conflicts: list[PortableImportConflict],
) -> None:
    signature = _work_signature(work)
    previous = signatures.get(work.source_work_id)
    if previous is not None and previous != signature:
        _append_conflict(
            conflicts,
            target="work",
            source_id=work.source_work_id,
            code="manifest_work_source_conflict",
            detail="One source_work_id carries contradictory portable Work identity",
        )
    else:
        signatures[work.source_work_id] = signature

    for identifier in work.identifiers:
        key = (normalize_text(identifier.scheme), normalize_identifier(identifier.value))
        _claim_external_identity(
            key,
            source_id=work.source_work_id,
            claims=identifier_claims,
            target="work",
            code="manifest_work_identifier_collision",
            detail="One durable Work identifier is claimed by multiple source_work_id values",
            conflicts=conflicts,
        )
    for source in work.sources:
        key = (source.provider, source.provider_record_id)
        _claim_external_identity(
            key,
            source_id=work.source_work_id,
            claims=source_claims,
            target="work",
            code="manifest_work_source_record_collision",
            detail="One provider Work record is claimed by multiple source_work_id values",
            conflicts=conflicts,
        )


def _register_edition(
    edition: PortableEditionIdentity,
    *,
    signatures: dict[UUID, Hashable],
    identifier_claims: dict[tuple[str, str], UUID],
    source_claims: dict[tuple[str, str], UUID],
    conflicts: list[PortableImportConflict],
) -> None:
    signature = _edition_signature(edition)
    previous = signatures.get(edition.source_edition_id)
    if previous is not None and previous != signature:
        _append_conflict(
            conflicts,
            target="edition",
            source_id=edition.source_edition_id,
            code="manifest_edition_source_conflict",
            detail="One source_edition_id carries contradictory portable Edition identity",
        )
    else:
        signatures[edition.source_edition_id] = signature

    for identifier in edition.identifiers:
        key = (normalize_text(identifier.scheme), normalize_identifier(identifier.value))
        _claim_external_identity(
            key,
            source_id=edition.source_edition_id,
            claims=identifier_claims,
            target="edition",
            code="manifest_edition_identifier_collision",
            detail="One durable Edition identifier is claimed by multiple source_edition_id values",
            conflicts=conflicts,
        )
    for source in edition.sources:
        key = (source.provider, source.provider_record_id)
        _claim_external_identity(
            key,
            source_id=edition.source_edition_id,
            claims=source_claims,
            target="edition",
            code="manifest_edition_source_record_collision",
            detail="One provider Edition record is claimed by multiple source_edition_id values",
            conflicts=conflicts,
        )


def _validate_assets(
    entry: PortableLibraryEntry,
    *,
    asset_ids: set[UUID],
    edition_signatures: dict[UUID, Hashable],
    edition_identifier_claims: dict[tuple[str, str], UUID],
    edition_source_claims: dict[tuple[str, str], UUID],
    document_signatures: dict[UUID, Hashable],
    conflicts: list[PortableImportConflict],
) -> None:
    for asset in entry.assets:
        if asset.source_asset_id in asset_ids:
            _append_conflict(
                conflicts,
                target="library_entry",
                source_id=entry.source_library_entry_id,
                code="manifest_duplicate_asset_source_id",
                detail="Asset source ids must be unique within one manifest",
            )
        asset_ids.add(asset.source_asset_id)

        _register_edition(
            asset.edition,
            signatures=edition_signatures,
            identifier_claims=edition_identifier_claims,
            source_claims=edition_source_claims,
            conflicts=conflicts,
        )
        if entry.edition is None or (
            asset.edition.source_edition_id != entry.edition.source_edition_id
            or _edition_signature(asset.edition) != _edition_signature(entry.edition)
        ):
            _append_conflict(
                conflicts,
                target="library_entry",
                source_id=entry.source_library_entry_id,
                code="manifest_asset_edition_conflict",
                detail="Asset edition identity disagrees with its parent library entry",
            )

        if asset.document is None:
            continue
        _register_document(
            asset.document,
            signatures=document_signatures,
            conflicts=conflicts,
        )
        if asset.content_sha256 is not None and (
            asset.content_sha256.casefold() != asset.document.source_sha256.casefold()
        ):
            _append_conflict(
                conflicts,
                target="document",
                source_id=asset.document.source_document_id,
                code="manifest_asset_document_sha_conflict",
                detail="Asset content SHA-256 disagrees with its embedded document identity",
            )
        if asset.format.casefold() != asset.document.format.casefold():
            _append_conflict(
                conflicts,
                target="document",
                source_id=asset.document.source_document_id,
                code="manifest_asset_document_format_conflict",
                detail="Asset format disagrees with its embedded document identity",
            )


def _validate_reading_states(
    entry: PortableLibraryEntry,
    *,
    reading_state_ids: set[UUID],
    bookmark_ids: set[UUID],
    highlight_ids: set[UUID],
    document_signatures: dict[UUID, Hashable],
    conflicts: list[PortableImportConflict],
) -> None:
    reading_documents: dict[Hashable, UUID] = {}
    for reading in entry.reading_states:
        if reading.source_reading_state_id in reading_state_ids:
            _append_conflict(
                conflicts,
                target="reading_state",
                source_id=reading.source_reading_state_id,
                code="manifest_duplicate_reading_state_source_id",
                detail="Reading-state source ids must be unique within one manifest",
            )
        reading_state_ids.add(reading.source_reading_state_id)
        _register_document(
            reading.document,
            signatures=document_signatures,
            conflicts=conflicts,
        )

        document_signature = _document_signature(reading.document)
        previous_reading = reading_documents.get(document_signature)
        if previous_reading is not None and previous_reading != reading.source_reading_state_id:
            _append_conflict(
                conflicts,
                target="reading_state",
                source_id=reading.source_reading_state_id,
                code="manifest_duplicate_reading_document_identity",
                detail=(
                    "One library entry contains multiple reader states "
                    "for the same document identity"
                ),
            )
        else:
            reading_documents[document_signature] = reading.source_reading_state_id

        bookmark_positions: set[tuple[int, int]] = set()
        for bookmark in reading.bookmarks:
            if bookmark.source_bookmark_id in bookmark_ids:
                _append_conflict(
                    conflicts,
                    target="reading_state",
                    source_id=reading.source_reading_state_id,
                    code="manifest_duplicate_bookmark_source_id",
                    detail="Bookmark source ids must be unique within one manifest",
                )
            bookmark_ids.add(bookmark.source_bookmark_id)
            position = (bookmark.section_ordinal, bookmark.char_offset)
            if position in bookmark_positions:
                _append_conflict(
                    conflicts,
                    target="reading_state",
                    source_id=reading.source_reading_state_id,
                    code="manifest_duplicate_bookmark_position",
                    detail="One reader state contains multiple bookmarks at the same coordinate",
                )
            bookmark_positions.add(position)

        highlight_ranges: set[tuple[int, int, int]] = set()
        for highlight in reading.highlights:
            if highlight.source_highlight_id in highlight_ids:
                _append_conflict(
                    conflicts,
                    target="reading_state",
                    source_id=reading.source_reading_state_id,
                    code="manifest_duplicate_highlight_source_id",
                    detail="Highlight source ids must be unique within one manifest",
                )
            highlight_ids.add(highlight.source_highlight_id)
            position = (highlight.section_ordinal, highlight.char_start, highlight.char_end)
            if position in highlight_ranges:
                _append_conflict(
                    conflicts,
                    target="reading_state",
                    source_id=reading.source_reading_state_id,
                    code="manifest_duplicate_highlight_range",
                    detail="One reader state contains multiple highlights at the same range",
                )
            highlight_ranges.add(position)


def _register_document(
    document: PortableDocumentIdentity,
    *,
    signatures: dict[UUID, Hashable],
    conflicts: list[PortableImportConflict],
) -> None:
    signature = _document_signature(document)
    previous = signatures.get(document.source_document_id)
    if previous is not None and previous != signature:
        _append_conflict(
            conflicts,
            target="document",
            source_id=document.source_document_id,
            code="manifest_document_source_conflict",
            detail="One source_document_id carries contradictory content/parser identity",
        )
    else:
        signatures[document.source_document_id] = signature


def _reject_duplicate_refs(
    entry: PortableLibraryEntry,
    *,
    conflicts: list[PortableImportConflict],
) -> None:
    if len(entry.collection_ids) != len(set(entry.collection_ids)):
        _append_conflict(
            conflicts,
            target="library_entry",
            source_id=entry.source_library_entry_id,
            code="manifest_duplicate_collection_reference",
            detail="Library entry repeats the same collection reference",
        )
    if len(entry.tag_ids) != len(set(entry.tag_ids)):
        _append_conflict(
            conflicts,
            target="library_entry",
            source_id=entry.source_library_entry_id,
            code="manifest_duplicate_tag_reference",
            detail="Library entry repeats the same tag reference",
        )


def _reject_duplicate_records(
    values: list[tuple[UUID, str]],
    *,
    target: ImportPlanTarget,
    code: str,
    detail: str,
    conflicts: list[PortableImportConflict],
) -> None:
    seen: set[UUID] = set()
    for source_id, _ in values:
        if source_id in seen:
            _append_conflict(
                conflicts,
                target=target,
                source_id=source_id,
                code=code,
                detail=detail,
            )
        seen.add(source_id)


def _reject_normalized_name_collisions(
    values: list[tuple[UUID, str]],
    *,
    target: ImportPlanTarget,
    code: str,
    conflicts: list[PortableImportConflict],
) -> None:
    seen: dict[str, UUID] = {}
    for source_id, name in values:
        normalized = normalize_text(name)
        previous = seen.get(normalized)
        if previous is not None and previous != source_id:
            _append_conflict(
                conflicts,
                target=target,
                source_id=source_id,
                code=code,
                detail="Multiple portable records collapse to the same destination-owned name",
            )
        else:
            seen[normalized] = source_id


def _claim_external_identity(
    key: tuple[str, str],
    *,
    source_id: UUID,
    claims: dict[tuple[str, str], UUID],
    target: ImportPlanTarget,
    code: str,
    detail: str,
    conflicts: list[PortableImportConflict],
) -> None:
    previous = claims.get(key)
    if previous is not None and previous != source_id:
        _append_conflict(
            conflicts,
            target=target,
            source_id=source_id,
            code=code,
            detail=detail,
        )
    else:
        claims[key] = source_id


def _work_signature(work: PortableWorkIdentity) -> Hashable:
    return (
        normalize_text(work.canonical_title),
        tuple(sorted(normalize_text(author) for author in work.authors)),
        tuple(sorted(normalize_text(subject) for subject in work.subjects)),
        tuple(
            sorted(
                (normalize_text(identifier.scheme), normalize_identifier(identifier.value))
                for identifier in work.identifiers
            )
        ),
        tuple(
            sorted(
                (
                    source.provider,
                    source.provider_record_id,
                    source.canonical_url,
                    source.relationship,
                )
                for source in work.sources
            )
        ),
    )


def _edition_signature(edition: PortableEditionIdentity) -> Hashable:
    return (
        normalize_text(edition.title),
        normalize_text(edition.language or ""),
        edition.publication_year,
        normalize_text(edition.publisher or ""),
        normalize_text(edition.edition_statement or ""),
        tuple(
            sorted(
                (normalize_text(identifier.scheme), normalize_identifier(identifier.value))
                for identifier in edition.identifiers
            )
        ),
        tuple(
            sorted(
                (
                    source.provider,
                    source.provider_record_id,
                    source.canonical_url,
                    source.relationship,
                )
                for source in edition.sources
            )
        ),
    )


def _document_signature(document: PortableDocumentIdentity) -> Hashable:
    return (
        document.source_sha256.casefold(),
        document.format.casefold(),
        document.parser_name,
        document.parser_version,
    )


def _append_conflict(
    conflicts: list[PortableImportConflict],
    *,
    target: ImportPlanTarget,
    source_id: UUID,
    code: str,
    detail: str,
) -> None:
    conflict = PortableImportConflict(
        target=target,
        source_id=source_id,
        code=code,
        detail=detail,
    )
    if conflict not in conflicts:
        conflicts.append(conflict)
