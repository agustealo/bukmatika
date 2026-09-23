import asyncio
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import Settings
from bukmatika.library.byte_import import (
    LibraryPortableByteImportService,
    PortableByteImportConflict,
    PortableByteImportDenied,
    PortableByteImportIntegrityError,
)
from bukmatika.library.byte_portability import (
    BytePortabilityDenied,
    BytePortabilityIntegrityError,
    LibraryBytePortabilityService,
)
from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.library.portability_apply import (
    LibraryPortabilityImportApplier,
    LibraryPortabilityImportApplyResponse,
)
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    LibraryPortabilityImportPlanResponse,
    PortableAssetManifest,
)
from bukmatika.library.portability_import import LibraryPortabilityImportPlanner

BUNDLE_MEDIA_TYPE = "application/vnd.bukmatika.library+zip"
_MANIFEST_MEMBER = "manifest.json"
_INDEX_MEMBER = "bundle.json"
_BYTE_PREFIX = "bytes/"


class PortabilityBundleError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class PortableBundleByte(BaseModel):
    source_library_entry_id: UUID
    source_asset_id: UUID
    member_name: str
    format: str
    media_type: str | None
    sha256: str
    byte_size: int


class PortableBundleOmission(BaseModel):
    source_library_entry_id: UUID
    source_asset_id: UUID
    code: str
    detail: str


class LibraryPortabilityBundleIndex(BaseModel):
    schema_version: Literal[1] = 1
    bundle_kind: Literal["bukmatika-library-bundle"] = "bukmatika-library-bundle"
    manifest_member: Literal["manifest.json"] = "manifest.json"
    bytes: list[PortableBundleByte]
    omissions: list[PortableBundleOmission]


class LibraryPortabilityBundlePlanResponse(BaseModel):
    schema_version: Literal[1] = 1
    mode: Literal["file-dry-run"] = "file-dry-run"
    plan: LibraryPortabilityImportPlanResponse
    included_bytes: list[PortableBundleByte]
    omitted_bytes: list[PortableBundleOmission]


class PortableBundleByteApplyResult(BaseModel):
    source_library_entry_id: UUID
    source_asset_id: UUID
    status: Literal["stored", "idempotent", "denied", "conflict", "integrity-error"]
    code: str | None = None
    detail: str | None = None
    destination_library_entry_id: UUID | None = None
    destination_asset_id: UUID | None = None
    acquisition_id: UUID | None = None
    stored_object_id: UUID | None = None


class LibraryPortabilityBundleApplyResponse(BaseModel):
    schema_version: Literal[1] = 1
    mode: Literal["file-apply"] = "file-apply"
    manifest: LibraryPortabilityImportApplyResponse
    bytes: list[PortableBundleByteApplyResult]
    source_omissions: list[PortableBundleOmission]
    content_complete: bool


@dataclass(frozen=True, slots=True)
class BundleExportArtifact:
    path: Path
    root: Path
    filename: str
    index: LibraryPortabilityBundleIndex

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@dataclass(frozen=True, slots=True)
class BundleInspection:
    manifest: LibraryPortabilityExportResponse
    index: LibraryPortabilityBundleIndex
    byte_paths: dict[tuple[UUID, UUID], Path]
    root: Path

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class LibraryPortabilityBundleService:
    """Thin file transport over the canonical portability and byte authorities."""

    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings
        self._portability = LibraryPortabilityService()
        self._planner = LibraryPortabilityImportPlanner()
        self._applier = LibraryPortabilityImportApplier()
        self._store = LocalObjectStore(settings.storage_root)
        self._byte_export = LibraryBytePortabilityService(path_resolver=self._store)
        self._byte_import = LibraryPortableByteImportService(
            storage=self._store,
            settings=settings,
        )

    async def export_bundle(self, *, principal_id: UUID) -> BundleExportArtifact:
        manifest = await self._portability.export(principal_id=principal_id)
        root = Path(tempfile.mkdtemp(prefix="bukmatika-export-"))
        staging_root = root / "staging"
        package_path = root / "library.bukmatika"
        included: list[PortableBundleByte] = []
        omissions: list[PortableBundleOmission] = []
        try:
            for entry in manifest.entries:
                for asset in entry.assets:
                    if asset.content_sha256 is None or asset.byte_size is None:
                        omissions.append(
                            PortableBundleOmission(
                                source_library_entry_id=entry.source_library_entry_id,
                                source_asset_id=asset.source_asset_id,
                                code="content_identity_unavailable",
                                detail=(
                                    "Canonical SHA-256 and byte size are unavailable, so no "
                                    "portable byte can be included."
                                ),
                            )
                        )
                        continue
                    try:
                        copied = await self._byte_export.copy_for_export(
                            principal_id=principal_id,
                            library_entry_id=entry.source_library_entry_id,
                            asset_id=asset.source_asset_id,
                            staging_root=staging_root,
                        )
                    except (BytePortabilityDenied, BytePortabilityIntegrityError) as exc:
                        omissions.append(
                            PortableBundleOmission(
                                source_library_entry_id=entry.source_library_entry_id,
                                source_asset_id=asset.source_asset_id,
                                code=exc.code,
                                detail=exc.detail,
                            )
                        )
                        continue
                    included.append(
                        PortableBundleByte(
                            source_library_entry_id=entry.source_library_entry_id,
                            source_asset_id=asset.source_asset_id,
                            member_name=f"{_BYTE_PREFIX}{copied.sha256}",
                            format=copied.format,
                            media_type=copied.media_type,
                            sha256=copied.sha256,
                            byte_size=copied.byte_size,
                        )
                    )

            index = LibraryPortabilityBundleIndex(
                bytes=included,
                omissions=omissions,
            )
            await asyncio.to_thread(
                self._write_bundle,
                package_path,
                manifest,
                index,
                staging_root,
            )
            filename = f"bukmatika-library-{manifest.exported_at:%Y%m%d-%H%M%S}.bukmatika"
            return BundleExportArtifact(
                path=package_path,
                root=root,
                filename=filename,
                index=index,
            )
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    async def inspect_bundle(self, *, bundle_path: Path) -> BundleInspection:
        extraction_root = Path(tempfile.mkdtemp(prefix="bukmatika-import-"))
        try:
            return await asyncio.to_thread(
                self._inspect_bundle_sync,
                bundle_path,
                extraction_root,
            )
        except Exception:
            shutil.rmtree(extraction_root, ignore_errors=True)
            raise

    async def plan_bundle(
        self,
        *,
        principal_id: UUID,
        inspection: BundleInspection,
    ) -> LibraryPortabilityBundlePlanResponse:
        plan = await self._planner.plan(
            principal_id=principal_id,
            manifest=inspection.manifest,
        )
        return LibraryPortabilityBundlePlanResponse(
            plan=plan,
            included_bytes=inspection.index.bytes,
            omitted_bytes=inspection.index.omissions,
        )

    async def apply_bundle(
        self,
        *,
        principal_id: UUID,
        inspection: BundleInspection,
    ) -> LibraryPortabilityBundleApplyResponse:
        manifest_result = await self._applier.apply(
            principal_id=principal_id,
            manifest=inspection.manifest,
        )
        if not manifest_result.committed:
            return LibraryPortabilityBundleApplyResponse(
                manifest=manifest_result,
                bytes=[],
                source_omissions=inspection.index.omissions,
                content_complete=False,
            )

        byte_results: list[PortableBundleByteApplyResult] = []
        for item in inspection.index.bytes:
            source_key = (item.source_library_entry_id, item.source_asset_id)
            source_path = inspection.byte_paths[source_key]
            try:
                result = await self._byte_import.import_byte(
                    principal_id=principal_id,
                    manifest=inspection.manifest,
                    source_library_entry_id=item.source_library_entry_id,
                    source_asset_id=item.source_asset_id,
                    source_path=source_path,
                )
            except PortableByteImportDenied as exc:
                byte_results.append(self._byte_failure(item, "denied", exc.code, exc.detail))
            except PortableByteImportConflict as exc:
                byte_results.append(self._byte_failure(item, "conflict", exc.code, exc.detail))
            except PortableByteImportIntegrityError as exc:
                byte_results.append(
                    self._byte_failure(item, "integrity-error", exc.code, exc.detail)
                )
            else:
                byte_results.append(
                    PortableBundleByteApplyResult(
                        source_library_entry_id=item.source_library_entry_id,
                        source_asset_id=item.source_asset_id,
                        status="idempotent" if result.idempotent else "stored",
                        destination_library_entry_id=result.library_entry_id,
                        destination_asset_id=result.asset_id,
                        acquisition_id=result.acquisition_id,
                        stored_object_id=result.stored_object_id,
                    )
                )

        content_complete = not inspection.index.omissions and all(
            result.status in {"stored", "idempotent"} for result in byte_results
        )
        return LibraryPortabilityBundleApplyResponse(
            manifest=manifest_result,
            bytes=byte_results,
            source_omissions=inspection.index.omissions,
            content_complete=content_complete,
        )

    async def receive_bundle(self, chunks: AsyncIterator[bytes]) -> tuple[Path, Path]:
        root = Path(tempfile.mkdtemp(prefix="bukmatika-upload-"))
        path = root / "upload.bukmatika"
        total = 0
        try:
            with path.open("xb") as handle:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self._settings.archive_max_uncompressed_bytes:
                        raise PortabilityBundleError(
                            "bundle_too_large",
                            "Portability bundle exceeds the configured archive size limit.",
                        )
                    handle.write(chunk)
            if total == 0:
                raise PortabilityBundleError("bundle_empty", "Portability bundle is empty.")
            return path, root
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise

    @staticmethod
    def cleanup_upload(root: Path) -> None:
        shutil.rmtree(root, ignore_errors=True)

    @staticmethod
    def _byte_failure(
        item: PortableBundleByte,
        status_value: Literal["denied", "conflict", "integrity-error"],
        code: str,
        detail: str,
    ) -> PortableBundleByteApplyResult:
        return PortableBundleByteApplyResult(
            source_library_entry_id=item.source_library_entry_id,
            source_asset_id=item.source_asset_id,
            status=status_value,
            code=code,
            detail=detail,
        )

    @staticmethod
    def _write_bundle(
        package_path: Path,
        manifest: LibraryPortabilityExportResponse,
        index: LibraryPortabilityBundleIndex,
        staging_root: Path,
    ) -> None:
        with zipfile.ZipFile(
            package_path,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            allowZip64=True,
        ) as archive:
            archive.writestr(_MANIFEST_MEMBER, manifest.model_dump_json(indent=2))
            archive.writestr(_INDEX_MEMBER, index.model_dump_json(indent=2))
            written_members: set[str] = set()
            for item in sorted(index.bytes, key=lambda value: value.member_name):
                if item.member_name in written_members:
                    continue
                source = staging_root / item.sha256
                archive.write(source, item.member_name)
                written_members.add(item.member_name)

    def _inspect_bundle_sync(
        self,
        bundle_path: Path,
        extraction_root: Path,
    ) -> BundleInspection:
        if bundle_path.is_symlink() or not bundle_path.is_file():
            raise PortabilityBundleError(
                "bundle_unavailable",
                "Portability bundle must be a regular file.",
            )
        try:
            archive = zipfile.ZipFile(bundle_path, mode="r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise PortabilityBundleError(
                "bundle_invalid_zip",
                "Portability bundle is not a valid ZIP container.",
            ) from exc

        with archive:
            infos = archive.infolist()
            if len(infos) > self._settings.archive_max_members:
                raise PortabilityBundleError(
                    "bundle_member_limit",
                    "Portability bundle contains too many archive members.",
                )
            info_by_name: dict[str, zipfile.ZipInfo] = {}
            total_uncompressed = 0
            for info in infos:
                self._validate_member_info(info)
                if info.filename in info_by_name:
                    raise PortabilityBundleError(
                        "bundle_duplicate_member",
                        f"Duplicate portability bundle member: {info.filename}",
                    )
                info_by_name[info.filename] = info
                total_uncompressed += info.file_size
                if total_uncompressed > self._settings.archive_max_uncompressed_bytes:
                    raise PortabilityBundleError(
                        "bundle_uncompressed_limit",
                        "Portability bundle exceeds the configured uncompressed size limit.",
                    )
                if info.file_size and (
                    info.file_size / max(info.compress_size, 1)
                    > self._settings.archive_max_compression_ratio
                ):
                    raise PortabilityBundleError(
                        "bundle_compression_ratio",
                        f"Portability bundle member is compressed beyond the safety limit: {info.filename}",
                    )

            manifest_info = self._required_metadata_member(info_by_name, _MANIFEST_MEMBER)
            index_info = self._required_metadata_member(info_by_name, _INDEX_MEMBER)
            try:
                manifest = LibraryPortabilityExportResponse.model_validate_json(
                    archive.read(manifest_info)
                )
                index = LibraryPortabilityBundleIndex.model_validate_json(archive.read(index_info))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                raise PortabilityBundleError(
                    "bundle_metadata_invalid",
                    "Portability bundle metadata does not match the supported schema.",
                ) from exc

            assets = self._manifest_assets(manifest)
            included_keys = {
                (item.source_library_entry_id, item.source_asset_id) for item in index.bytes
            }
            omitted_keys = {
                (item.source_library_entry_id, item.source_asset_id) for item in index.omissions
            }
            if len(included_keys) != len(index.bytes) or len(omitted_keys) != len(index.omissions):
                raise PortabilityBundleError(
                    "bundle_duplicate_asset",
                    "Each manifest asset must have exactly one bundle byte disposition.",
                )
            if included_keys & omitted_keys or included_keys | omitted_keys != set(assets):
                raise PortabilityBundleError(
                    "bundle_asset_coverage",
                    "Bundle byte index must account for every manifest asset exactly once.",
                )

            allowed_names = {_MANIFEST_MEMBER, _INDEX_MEMBER}
            byte_paths: dict[tuple[UUID, UUID], Path] = {}
            extracted_members: dict[str, Path] = {}
            for item in index.bytes:
                key = (item.source_library_entry_id, item.source_asset_id)
                asset = assets[key]
                self._validate_index_item(item, asset)
                info = info_by_name.get(item.member_name)
                if info is None or info.is_dir():
                    raise PortabilityBundleError(
                        "bundle_byte_missing",
                        f"Bundle byte member is missing: {item.member_name}",
                    )
                if info.file_size != item.byte_size:
                    raise PortabilityBundleError(
                        "bundle_byte_size_mismatch",
                        f"Bundle member size disagrees with its index: {item.member_name}",
                    )
                allowed_names.add(item.member_name)
                extracted = extracted_members.get(item.member_name)
                if extracted is None:
                    extracted = extraction_root / item.sha256
                    actual_sha, actual_size = self._extract_and_fingerprint(
                        archive,
                        info,
                        extracted,
                        self._settings.acquisition_max_bytes,
                    )
                    if actual_sha != item.sha256 or actual_size != item.byte_size:
                        raise PortabilityBundleError(
                            "bundle_byte_identity_mismatch",
                            f"Bundle bytes do not match their declared identity: {item.member_name}",
                        )
                    extracted_members[item.member_name] = extracted
                byte_paths[key] = extracted

            unknown = set(info_by_name) - allowed_names
            if unknown:
                raise PortabilityBundleError(
                    "bundle_unknown_member",
                    f"Portability bundle contains unindexed members: {', '.join(sorted(unknown))}",
                )

        return BundleInspection(
            manifest=manifest,
            index=index,
            byte_paths=byte_paths,
            root=extraction_root,
        )

    def _required_metadata_member(
        self,
        info_by_name: dict[str, zipfile.ZipInfo],
        name: str,
    ) -> zipfile.ZipInfo:
        info = info_by_name.get(name)
        if info is None or info.is_dir():
            raise PortabilityBundleError(
                "bundle_metadata_missing",
                f"Portability bundle is missing {name}.",
            )
        if info.file_size > self._settings.processing_max_bytes:
            raise PortabilityBundleError(
                "bundle_metadata_too_large",
                f"Portability metadata member exceeds the configured processing limit: {name}",
            )
        return info

    @staticmethod
    def _manifest_assets(
        manifest: LibraryPortabilityExportResponse,
    ) -> dict[tuple[UUID, UUID], PortableAssetManifest]:
        assets: dict[tuple[UUID, UUID], PortableAssetManifest] = {}
        for entry in manifest.entries:
            for asset in entry.assets:
                key = (entry.source_library_entry_id, asset.source_asset_id)
                if key in assets:
                    raise PortabilityBundleError(
                        "bundle_manifest_duplicate_asset",
                        "Manifest repeats the same source library-entry/asset identity.",
                    )
                assets[key] = asset
        return assets

    @staticmethod
    def _validate_index_item(
        item: PortableBundleByte,
        asset: PortableAssetManifest,
    ) -> None:
        expected_name = f"{_BYTE_PREFIX}{item.sha256}"
        if item.member_name != expected_name:
            raise PortabilityBundleError(
                "bundle_byte_member_invalid",
                "Portable byte member name must be derived from its SHA-256 identity.",
            )
        if asset.content_sha256 is None or asset.byte_size is None:
            raise PortabilityBundleError(
                "bundle_manifest_content_identity_missing",
                "Included bytes require canonical content SHA-256 and byte size in the manifest.",
            )
        if (
            item.sha256.casefold() != asset.content_sha256.casefold()
            or item.byte_size != asset.byte_size
            or item.format.casefold() != asset.format.casefold()
            or LibraryPortabilityBundleService._media_type(item.media_type)
            != LibraryPortabilityBundleService._media_type(asset.media_type)
        ):
            raise PortabilityBundleError(
                "bundle_manifest_byte_disagreement",
                "Bundle byte index disagrees with canonical manifest identity.",
            )

    @staticmethod
    def _validate_member_info(info: zipfile.ZipInfo) -> None:
        name = info.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise PortabilityBundleError(
                "bundle_member_path_invalid",
                f"Unsafe portability bundle member path: {name!r}",
            )
        if info.flag_bits & 0x1:
            raise PortabilityBundleError(
                "bundle_encrypted_member",
                f"Encrypted portability bundle members are not supported: {name}",
            )
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise PortabilityBundleError(
                "bundle_symlink_member",
                f"Symbolic links are not allowed in portability bundles: {name}",
            )

    @staticmethod
    def _extract_and_fingerprint(
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        destination: Path,
        max_bytes: int,
    ) -> tuple[str, int]:
        if info.file_size > max_bytes:
            raise PortabilityBundleError(
                "bundle_byte_too_large",
                "Portable byte member exceeds the configured acquisition limit.",
            )
        digest = hashlib.sha256()
        total = 0
        with archive.open(info, "r") as source, destination.open("xb") as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise PortabilityBundleError(
                        "bundle_byte_too_large",
                        "Portable byte member exceeds the configured acquisition limit.",
                    )
                digest.update(chunk)
                target.write(chunk)
        return digest.hexdigest(), total

    @staticmethod
    def _media_type(value: str | None) -> str | None:
        return value.split(";", 1)[0].strip().casefold() if value else None
