import asyncio
import hashlib
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import AcquisitionObjectStore, StoredObjectPathResolver
from bukmatika.acquisition.verification import FormatVerificationError, verify_download
from bukmatika.config import Settings
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableAssetManifest,
    PortableImportEntryPlan,
    PortableLibraryEntry,
)
from bukmatika.library.portability_import import LibraryPortabilityImportPlanner
from bukmatika.library.portability_validation import validate_manifest_consistency
from bukmatika.normalization import normalize_identifier
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition import AcquisitionRepository, AcquisitionStateConflict
from bukmatika.persistence.library_byte_import import LibraryPortableByteImportRepository
from bukmatika.persistence.models import Acquisition, Asset, RightsDecision, StoredObject

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PortableByteObjectStore(AcquisitionObjectStore, StoredObjectPathResolver, Protocol):
    pass


class PortableByteImportDenied(PermissionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PortableByteImportConflict(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PortableByteImportIntegrityError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class PortableByteImportResult:
    library_entry_id: UUID
    asset_id: UUID
    acquisition_id: UUID
    stored_object_id: UUID
    rights_decision_id: UUID
    sha256: str
    byte_size: int
    storage_key: str
    idempotent: bool


@dataclass(frozen=True, slots=True)
class _Destination:
    asset: PortableAssetManifest
    work_id: UUID
    edition_id: UUID
    library_entry_id: UUID
    asset_id: UUID


class LibraryPortableByteImportService:
    """Ingest portable bytes through destination-local canonical authorities.

    Portable source rights remain provenance only. The destination must already
    resolve the portable Work, Edition, and Asset identity, the principal must
    already own the matching library entry, and the latest local RightsDecision
    must explicitly grant retention before canonical association.
    """

    def __init__(
        self,
        *,
        storage: PortableByteObjectStore,
        settings: Settings,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._session_scope = session_scope_factory

    async def import_byte(
        self,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
        source_library_entry_id: UUID,
        source_asset_id: UUID,
        source_path: Path,
    ) -> PortableByteImportResult:
        expected_sha256, expected_size = self._required_content_identity(
            manifest=manifest,
            source_library_entry_id=source_library_entry_id,
            source_asset_id=source_asset_id,
        )
        if expected_size > self._settings.acquisition_max_bytes:
            raise PortableByteImportIntegrityError(
                "portable_byte_too_large",
                "Portable byte size exceeds the configured acquisition limit.",
            )

        destination = await self._resolve_destination(
            principal_id=principal_id,
            manifest=manifest,
            source_library_entry_id=source_library_entry_id,
            source_asset_id=source_asset_id,
        )
        await self._preflight_destination(
            principal_id=principal_id,
            destination=destination,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )

        temp_path = await self._storage.create_temp_path(uuid4())
        try:
            actual_sha256, actual_size = await asyncio.to_thread(
                self._copy_and_fingerprint,
                source_path,
                temp_path,
                self._settings.acquisition_max_bytes,
            )
            if actual_size != expected_size or actual_sha256 != expected_sha256:
                raise PortableByteImportIntegrityError(
                    "portable_content_identity_mismatch",
                    "Portable bytes do not match the manifest SHA-256 and byte size.",
                )
            await self._verify_staged_format(temp_path, destination.asset)
            return await self._commit_staged_bytes(
                principal_id=principal_id,
                manifest=manifest,
                original_destination=destination,
                source_library_entry_id=source_library_entry_id,
                source_asset_id=source_asset_id,
                temp_path=temp_path,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
        finally:
            with suppress(FileNotFoundError):
                await self._storage.discard(temp_path)

    async def _verify_staged_format(
        self,
        path: Path,
        asset: PortableAssetManifest,
    ) -> None:
        try:
            await asyncio.to_thread(
                verify_download,
                path,
                expected_format=asset.format,
                media_type=asset.media_type,
                archive_max_members=self._settings.archive_max_members,
                archive_max_uncompressed_bytes=self._settings.archive_max_uncompressed_bytes,
                archive_max_compression_ratio=self._settings.archive_max_compression_ratio,
            )
        except FormatVerificationError as exc:
            raise PortableByteImportIntegrityError(
                "portable_format_verification_failed",
                str(exc),
            ) from exc

    async def _commit_staged_bytes(
        self,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
        original_destination: _Destination,
        source_library_entry_id: UUID,
        source_asset_id: UUID,
        temp_path: Path,
        expected_sha256: str,
        expected_size: int,
    ) -> PortableByteImportResult:
        async with self._session_scope() as database_session:
            current = await self._resolve_destination_in_session(
                database_session,
                principal_id=principal_id,
                manifest=manifest,
                source_library_entry_id=source_library_entry_id,
                source_asset_id=source_asset_id,
            )
            if self._destination_identity(current) != self._destination_identity(
                original_destination
            ):
                raise PortableByteImportConflict(
                    "portable_destination_changed",
                    (
                        "Portable identity resolved to a different canonical "
                        "destination before commit."
                    ),
                )

            repository = LibraryPortableByteImportRepository(database_session)
            owned = await repository.lock_owned_asset(
                principal_id=principal_id,
                library_entry_id=current.library_entry_id,
                work_id=current.work_id,
                edition_id=current.edition_id,
                asset_id=current.asset_id,
            )
            if owned is None:
                raise PortableByteImportDenied(
                    "portable_destination_unowned",
                    "The resolved destination asset is not owned through this library entry.",
                )
            self._require_asset_metadata(owned.asset, current.asset)
            rights = self._require_retain_permission(
                await repository.latest_rights_decision(owned.asset.id)
            )
            acquisition = await repository.acquisition_for_asset(owned.asset.id)
            existing = await self._existing_result(
                repository=repository,
                destination=current,
                asset=owned.asset,
                acquisition=acquisition,
                rights=rights,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
            if existing is not None:
                return existing
            self._require_ingestible_acquisition(owned.asset, acquisition)

            stored_file = await self._storage.commit(
                temp_path,
                sha256=expected_sha256,
                format_name=current.asset.format,
            )
            await self._verify_canonical_path(
                stored_file.path,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )

            # The portable snapshot never authorizes retention. Re-read the
            # destination-local decision after the content-addressed handoff and
            # before any canonical database association is committed.
            rights = self._require_retain_permission(
                await repository.latest_rights_decision(owned.asset.id)
            )
            acquisition_repository = AcquisitionRepository(database_session)
            if acquisition is None:
                try:
                    acquisition = await acquisition_repository.create_or_get_acquisition(
                        owned.asset
                    )
                except ValueError as exc:
                    raise PortableByteImportConflict(
                        "portable_acquisition_unrepresentable",
                        (
                            "The canonical asset has no acquisition locator, so the "
                            "current acquisition authority cannot represent this import "
                            "honestly."
                        ),
                    ) from exc

            if acquisition.status == "stored":
                raise PortableByteImportConflict(
                    "portable_acquisition_state_conflict",
                    (
                        "Acquisition became stored with different canonical state "
                        "before commit."
                    ),
                )
            try:
                acquisition = await acquisition_repository.begin_attempt(acquisition.id)
                acquisition = await acquisition_repository.mark_downloading(
                    acquisition.id,
                    rights.id,
                )
                media_type = self._effective_media_type(owned.asset, current.asset)
                acquisition = await acquisition_repository.mark_verifying(
                    acquisition.id,
                    bytes_received=expected_size,
                    sha256=expected_sha256,
                    media_type=media_type,
                    redirect_count=0,
                )
            except AcquisitionStateConflict as exc:
                raise PortableByteImportConflict(
                    "portable_acquisition_state_conflict",
                    str(exc),
                ) from exc

            stored_object = await acquisition_repository.upsert_stored_object(
                sha256=expected_sha256,
                storage_key=stored_file.storage_key,
                byte_size=expected_size,
                media_type=media_type,
            )
            self._require_stored_object_metadata(
                stored_object,
                storage_key=stored_file.storage_key,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                expected_media_type=media_type,
            )
            try:
                acquisition = await acquisition_repository.mark_stored(
                    acquisition.id,
                    asset_id=owned.asset.id,
                    stored_object_id=stored_object.id,
                    byte_size=expected_size,
                )
            except AcquisitionStateConflict as exc:
                raise PortableByteImportConflict(
                    "portable_acquisition_state_conflict",
                    str(exc),
                ) from exc

            return PortableByteImportResult(
                library_entry_id=current.library_entry_id,
                asset_id=owned.asset.id,
                acquisition_id=acquisition.id,
                stored_object_id=stored_object.id,
                rights_decision_id=rights.id,
                sha256=expected_sha256,
                byte_size=expected_size,
                storage_key=stored_object.storage_key,
                idempotent=False,
            )

    async def _preflight_destination(
        self,
        *,
        principal_id: UUID,
        destination: _Destination,
        expected_sha256: str,
        expected_size: int,
    ) -> None:
        async with self._session_scope() as database_session:
            repository = LibraryPortableByteImportRepository(database_session)
            owned = await repository.lock_owned_asset(
                principal_id=principal_id,
                library_entry_id=destination.library_entry_id,
                work_id=destination.work_id,
                edition_id=destination.edition_id,
                asset_id=destination.asset_id,
            )
            if owned is None:
                raise PortableByteImportDenied(
                    "portable_destination_unowned",
                    "The resolved destination asset is not owned through this library entry.",
                )
            self._require_asset_metadata(owned.asset, destination.asset)
            rights = self._require_retain_permission(
                await repository.latest_rights_decision(owned.asset.id)
            )
            acquisition = await repository.acquisition_for_asset(owned.asset.id)
            existing = await self._existing_result(
                repository=repository,
                destination=destination,
                asset=owned.asset,
                acquisition=acquisition,
                rights=rights,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
            )
            if existing is None:
                self._require_ingestible_acquisition(owned.asset, acquisition)

    async def _resolve_destination(
        self,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
        source_library_entry_id: UUID,
        source_asset_id: UUID,
    ) -> _Destination:
        async with self._session_scope() as database_session:
            return await self._resolve_destination_in_session(
                database_session,
                principal_id=principal_id,
                manifest=manifest,
                source_library_entry_id=source_library_entry_id,
                source_asset_id=source_asset_id,
            )

    async def _resolve_destination_in_session(
        self,
        database_session: AsyncSession,
        *,
        principal_id: UUID,
        manifest: LibraryPortabilityExportResponse,
        source_library_entry_id: UUID,
        source_asset_id: UUID,
    ) -> _Destination:
        manifest_conflicts = validate_manifest_consistency(manifest)
        if manifest_conflicts:
            conflict = manifest_conflicts[0]
            raise PortableByteImportConflict(conflict.code, conflict.detail)

        entry, asset = self._portable_asset(
            manifest,
            source_library_entry_id=source_library_entry_id,
            source_asset_id=source_asset_id,
        )
        planner = LibraryPortabilityImportPlanner(
            session_scope_factory=self._bound_scope(database_session)
        )
        plan = await planner.plan(principal_id=principal_id, manifest=manifest)
        entry_plan = self._entry_plan(
            manifest,
            plan.entries,
            source_library_entry_id=source_library_entry_id,
        )
        if entry_plan.work.action != "match" or entry_plan.work.destination_id is None:
            raise PortableByteImportConflict(
                "portable_work_unresolved",
                "Portable bytes require an already resolved canonical destination Work.",
            )
        if (
            entry.edition is None
            or entry_plan.edition is None
            or entry_plan.edition.action != "match"
            or entry_plan.edition.destination_id is None
        ):
            raise PortableByteImportConflict(
                "portable_edition_unresolved",
                "Portable bytes require an already resolved canonical destination Edition.",
            )
        if (
            entry_plan.library_entry.action != "match"
            or entry_plan.library_entry.destination_id is None
        ):
            raise PortableByteImportDenied(
                "portable_destination_unowned",
                (
                    "Portable bytes require an existing principal-owned destination "
                    "library entry."
                ),
            )

        repository = LibraryPortableByteImportRepository(database_session)
        asset_id = await self._resolve_asset_id(
            repository,
            asset=asset,
            edition_id=entry_plan.edition.destination_id,
        )
        return _Destination(
            asset=asset,
            work_id=entry_plan.work.destination_id,
            edition_id=entry_plan.edition.destination_id,
            library_entry_id=entry_plan.library_entry.destination_id,
            asset_id=asset_id,
        )

    async def _resolve_asset_id(
        self,
        repository: LibraryPortableByteImportRepository,
        *,
        asset: PortableAssetManifest,
        edition_id: UUID,
    ) -> UUID:
        durable_candidates: set[UUID] = set()
        for identifier in asset.identifiers:
            durable_candidates.update(
                await repository.asset_ids_for_identifier(
                    identifier.scheme,
                    normalize_identifier(identifier.value),
                )
            )
        durable_candidates.update(
            await repository.asset_ids_for_sources(
                [(source.provider, source.provider_record_id) for source in asset.sources]
            )
        )
        if len(durable_candidates) > 1:
            raise PortableByteImportConflict(
                "portable_asset_identity_conflict",
                "Portable Asset evidence resolves to multiple destination assets.",
            )
        if len(durable_candidates) == 1:
            destination_id = next(iter(durable_candidates))
            destination = await repository.asset(destination_id)
            if destination is None or destination.edition_id != edition_id:
                raise PortableByteImportConflict(
                    "portable_asset_edition_conflict",
                    "Durable Asset evidence resolves outside the destination Edition.",
                )
            return destination_id

        if asset.content_sha256 is not None:
            sha_candidates = await repository.asset_ids_for_content_sha(
                sha256=asset.content_sha256.casefold(),
                edition_id=edition_id,
            )
            if len(sha_candidates) > 1:
                raise PortableByteImportConflict(
                    "portable_asset_sha_ambiguous",
                    (
                        "Portable content SHA-256 matches multiple assets in the "
                        "destination Edition."
                    ),
                )
            if len(sha_candidates) == 1:
                return next(iter(sha_candidates))

        metadata_candidates = [
            candidate
            for candidate in await repository.assets_for_edition(edition_id)
            if self._asset_metadata_matches(candidate, asset)
        ]
        if not metadata_candidates:
            raise PortableByteImportConflict(
                "portable_asset_unresolved",
                "No canonical destination Asset matches the portable identity.",
            )
        if len(metadata_candidates) > 1:
            raise PortableByteImportConflict(
                "portable_asset_identity_ambiguous",
                "Portable Asset metadata matches multiple canonical destination assets.",
            )
        return metadata_candidates[0].id

    async def _existing_result(
        self,
        *,
        repository: LibraryPortableByteImportRepository,
        destination: _Destination,
        asset: Asset,
        acquisition: Acquisition | None,
        rights: RightsDecision,
        expected_sha256: str,
        expected_size: int,
    ) -> PortableByteImportResult | None:
        if asset.stored_object_id is None:
            if acquisition is not None and acquisition.status == "stored":
                raise PortableByteImportConflict(
                    "portable_canonical_storage_conflict",
                    (
                        "Stored acquisition is inconsistent with the destination "
                        "Asset storage link."
                    ),
                )
            return None
        if acquisition is None or acquisition.status != "stored":
            raise PortableByteImportConflict(
                "portable_canonical_storage_conflict",
                "Destination Asset storage exists without a matching stored acquisition.",
            )
        if acquisition.stored_object_id != asset.stored_object_id:
            raise PortableByteImportConflict(
                "portable_canonical_storage_conflict",
                "Asset and acquisition disagree on the canonical stored object.",
            )

        stored = await repository.stored_object(asset.stored_object_id)
        if stored is None:
            raise PortableByteImportConflict(
                "portable_canonical_storage_conflict",
                "Destination Asset references a missing canonical stored object.",
            )
        self._require_stored_object_metadata(
            stored,
            storage_key=stored.storage_key,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            expected_media_type=self._effective_media_type(asset, destination.asset),
        )
        try:
            path = await self._storage.resolve_path(stored.storage_key)
        except (FileNotFoundError, ValueError) as exc:
            raise PortableByteImportIntegrityError(
                "portable_canonical_object_unavailable",
                (
                    "Existing canonical stored bytes are unavailable or outside the "
                    "object namespace."
                ),
            ) from exc
        await self._verify_canonical_path(
            path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
        return PortableByteImportResult(
            library_entry_id=destination.library_entry_id,
            asset_id=asset.id,
            acquisition_id=acquisition.id,
            stored_object_id=stored.id,
            rights_decision_id=rights.id,
            sha256=expected_sha256,
            byte_size=expected_size,
            storage_key=stored.storage_key,
            idempotent=True,
        )

    @staticmethod
    def _required_content_identity(
        *,
        manifest: LibraryPortabilityExportResponse,
        source_library_entry_id: UUID,
        source_asset_id: UUID,
    ) -> tuple[str, int]:
        _, asset = LibraryPortableByteImportService._portable_asset(
            manifest,
            source_library_entry_id=source_library_entry_id,
            source_asset_id=source_asset_id,
        )
        if asset.content_sha256 is None or asset.byte_size is None:
            raise PortableByteImportIntegrityError(
                "portable_content_identity_missing",
                "Portable byte ingestion requires canonical SHA-256 and byte size metadata.",
            )
        sha256 = asset.content_sha256.casefold()
        try:
            raw_digest = bytes.fromhex(sha256)
        except ValueError as exc:
            raise PortableByteImportIntegrityError(
                "portable_content_identity_invalid",
                "Portable content SHA-256 is not valid hexadecimal.",
            ) from exc
        if len(raw_digest) != 32 or len(sha256) != 64:
            raise PortableByteImportIntegrityError(
                "portable_content_identity_invalid",
                "Portable content SHA-256 must contain exactly 64 hexadecimal characters.",
            )
        return sha256, asset.byte_size

    @staticmethod
    def _portable_asset(
        manifest: LibraryPortabilityExportResponse,
        *,
        source_library_entry_id: UUID,
        source_asset_id: UUID,
    ) -> tuple[PortableLibraryEntry, PortableAssetManifest]:
        entries = [
            entry
            for entry in manifest.entries
            if entry.source_library_entry_id == source_library_entry_id
        ]
        if len(entries) != 1:
            raise PortableByteImportConflict(
                "portable_library_entry_unresolved",
                "Portable library entry must resolve exactly once within the manifest.",
            )
        entry = entries[0]
        assets = [asset for asset in entry.assets if asset.source_asset_id == source_asset_id]
        if len(assets) != 1:
            raise PortableByteImportConflict(
                "portable_source_asset_unresolved",
                "Portable source Asset must resolve exactly once within its library entry.",
            )
        return entry, assets[0]

    @staticmethod
    def _entry_plan(
        manifest: LibraryPortabilityExportResponse,
        plans: list[PortableImportEntryPlan],
        *,
        source_library_entry_id: UUID,
    ) -> PortableImportEntryPlan:
        for entry, plan in zip(manifest.entries, plans, strict=True):
            if entry.source_library_entry_id == source_library_entry_id:
                return plan
        raise PortableByteImportConflict(
            "portable_library_entry_unresolved",
            "Import planner omitted the requested portable library entry.",
        )

    @staticmethod
    def _require_retain_permission(rights: RightsDecision | None) -> RightsDecision:
        if rights is None:
            raise PortableByteImportDenied(
                "portable_rights_missing",
                "No destination-local canonical rights decision authorizes byte retention.",
            )
        if rights.permissions.get("retain") is not True:
            raise PortableByteImportDenied(
                "portable_rights_retain_denied",
                (
                    "The latest destination-local canonical rights decision does not "
                    "allow retention."
                ),
            )
        return rights

    @staticmethod
    def _require_ingestible_acquisition(
        asset: Asset,
        acquisition: Acquisition | None,
    ) -> None:
        if acquisition is None:
            if asset.remote_url is None:
                raise PortableByteImportConflict(
                    "portable_acquisition_unrepresentable",
                    (
                        "The canonical Asset has no acquisition locator, so the current "
                        "acquisition authority cannot represent this import honestly."
                    ),
                )
            return
        if acquisition.status in {"resolving", "downloading", "verifying"}:
            raise PortableByteImportConflict(
                "portable_acquisition_active",
                "The canonical Asset already has an active acquisition attempt.",
            )
        if acquisition.status == "quarantined":
            raise PortableByteImportConflict(
                "portable_acquisition_quarantined",
                (
                    "A quarantined canonical acquisition must be resolved before "
                    "portable ingestion."
                ),
            )
        if acquisition.status == "stored":
            raise PortableByteImportConflict(
                "portable_canonical_storage_conflict",
                "Stored acquisition is inconsistent with the Asset storage link.",
            )

    @staticmethod
    def _require_asset_metadata(asset: Asset, portable: PortableAssetManifest) -> None:
        if asset.format.casefold() != portable.format.casefold():
            raise PortableByteImportConflict(
                "portable_asset_format_conflict",
                "Destination Asset format disagrees with portable Asset identity.",
            )
        if (
            asset.media_type is not None
            and portable.media_type is not None
            and LibraryPortableByteImportService._normalized_media_type(asset.media_type)
            != LibraryPortableByteImportService._normalized_media_type(portable.media_type)
        ):
            raise PortableByteImportConflict(
                "portable_asset_media_type_conflict",
                "Destination Asset media type disagrees with portable Asset identity.",
            )
        if (
            asset.byte_size is not None
            and portable.byte_size is not None
            and asset.byte_size != portable.byte_size
        ):
            raise PortableByteImportConflict(
                "portable_asset_size_conflict",
                "Destination Asset byte size disagrees with portable Asset identity.",
            )

    @staticmethod
    def _asset_metadata_matches(asset: Asset, portable: PortableAssetManifest) -> bool:
        if asset.format.casefold() != portable.format.casefold():
            return False
        if (
            asset.media_type is not None
            and portable.media_type is not None
            and LibraryPortableByteImportService._normalized_media_type(asset.media_type)
            != LibraryPortableByteImportService._normalized_media_type(portable.media_type)
        ):
            return False
        return not (
            asset.byte_size is not None
            and portable.byte_size is not None
            and asset.byte_size != portable.byte_size
        )

    @staticmethod
    def _require_stored_object_metadata(
        stored: StoredObject,
        *,
        storage_key: str,
        expected_sha256: str,
        expected_size: int,
        expected_media_type: str | None,
    ) -> None:
        if stored.sha256.casefold() != expected_sha256 or stored.byte_size != expected_size:
            raise PortableByteImportIntegrityError(
                "portable_stored_object_conflict",
                (
                    "Canonical stored-object SHA-256 or byte size disagrees with "
                    "portable content."
                ),
            )
        if stored.storage_key != storage_key:
            raise PortableByteImportIntegrityError(
                "portable_stored_object_conflict",
                (
                    "Canonical stored-object key disagrees with the content-addressed "
                    "storage path."
                ),
            )
        if (
            stored.media_type is not None
            and expected_media_type is not None
            and LibraryPortableByteImportService._normalized_media_type(stored.media_type)
            != LibraryPortableByteImportService._normalized_media_type(expected_media_type)
        ):
            raise PortableByteImportIntegrityError(
                "portable_stored_object_conflict",
                "Canonical stored-object media type disagrees with the destination Asset.",
            )

    async def _verify_canonical_path(
        self,
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> None:
        try:
            actual_sha256, actual_size = await asyncio.to_thread(self._fingerprint, path)
        except OSError as exc:
            raise PortableByteImportIntegrityError(
                "portable_canonical_object_unavailable",
                "Canonical stored bytes could not be read as a regular file.",
            ) from exc
        if actual_sha256 != expected_sha256 or actual_size != expected_size:
            raise PortableByteImportIntegrityError(
                "portable_canonical_object_integrity_failed",
                (
                    "Canonical stored bytes do not match the expected SHA-256 and "
                    "byte size."
                ),
            )

    @staticmethod
    def _copy_and_fingerprint(
        source: Path,
        destination: Path,
        max_bytes: int,
    ) -> tuple[str, int]:
        if source.is_symlink():
            raise PortableByteImportIntegrityError(
                "portable_source_unsafe",
                "Portable source bytes must not be supplied through a symbolic link.",
            )
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError as exc:
            raise PortableByteImportIntegrityError(
                "portable_source_unavailable",
                "Portable source bytes do not exist.",
            ) from exc
        if not resolved.is_file():
            raise PortableByteImportIntegrityError(
                "portable_source_unsafe",
                "Portable source bytes must be a regular file.",
            )

        digest = hashlib.sha256()
        byte_size = 0
        try:
            with resolved.open("rb") as input_file, destination.open("wb") as output_file:
                while chunk := input_file.read(1024 * 1024):
                    byte_size += len(chunk)
                    if byte_size > max_bytes:
                        raise PortableByteImportIntegrityError(
                            "portable_byte_too_large",
                            "Portable byte size exceeds the configured acquisition limit.",
                        )
                    digest.update(chunk)
                    output_file.write(chunk)
                output_file.flush()
                os.fsync(output_file.fileno())
        except BaseException:
            with suppress(FileNotFoundError):
                destination.unlink()
            raise
        return digest.hexdigest(), byte_size

    @staticmethod
    def _fingerprint(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
        return digest.hexdigest(), byte_size

    @staticmethod
    def _effective_media_type(
        asset: Asset,
        portable: PortableAssetManifest,
    ) -> str | None:
        return asset.media_type if asset.media_type is not None else portable.media_type

    @staticmethod
    def _normalized_media_type(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def _destination_identity(destination: _Destination) -> tuple[UUID, UUID, UUID, UUID]:
        return (
            destination.work_id,
            destination.edition_id,
            destination.library_entry_id,
            destination.asset_id,
        )

    @staticmethod
    def _bound_scope(
        session: AsyncSession,
    ) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
        @asynccontextmanager
        async def scope() -> AsyncIterator[AsyncSession]:
            yield session

        return scope
