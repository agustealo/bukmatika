import asyncio
import hashlib
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import StoredObjectPathResolver
from bukmatika.persistence import session_scope
from bukmatika.persistence.library_byte_portability import (
    BytePortabilityCandidate,
    LibraryBytePortabilityRepository,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class BytePortabilityDenied(PermissionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class BytePortabilityIntegrityError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class AuthorizedPortableByte:
    asset_id: UUID
    stored_object_id: UUID
    rights_decision_id: UUID
    format: str
    media_type: str | None
    sha256: str
    byte_size: int
    source_path: Path


@dataclass(frozen=True, slots=True)
class PortableByteCopy:
    asset_id: UUID
    rights_decision_id: UUID
    format: str
    media_type: str | None
    sha256: str
    byte_size: int
    path: Path


class LibraryBytePortabilityService:
    """Rights-gated handoff of verified canonical bytes for future portability bundles.

    Retained bytes stay under the canonical object store. This service only permits a
    byte to leave that store when the principal owns the relevant library identity and
    the latest destination-local canonical rights decision explicitly grants `export`.
    """

    def __init__(
        self,
        *,
        path_resolver: StoredObjectPathResolver,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._path_resolver = path_resolver
        self._session_scope = session_scope_factory

    async def authorize_export(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        asset_id: UUID,
    ) -> AuthorizedPortableByte:
        async with self._session_scope() as database_session:
            candidate = await LibraryBytePortabilityRepository(
                database_session
            ).export_candidate(
                principal_id=principal_id,
                library_entry_id=library_entry_id,
                asset_id=asset_id,
            )

        if candidate is None:
            raise BytePortabilityDenied(
                "asset_unavailable",
                (
                    "The requested asset is not an owned, verified stored asset "
                    "for this library entry."
                ),
            )
        self._require_export_permission(candidate)
        self._require_consistent_metadata(candidate)

        try:
            source_path = await self._path_resolver.resolve_path(candidate.storage_key)
        except (FileNotFoundError, ValueError) as exc:
            raise BytePortabilityIntegrityError(
                "stored_object_unavailable",
                "The canonical stored object is unavailable or outside the object namespace.",
            ) from exc

        await self._verify_path(
            source_path,
            expected_sha256=candidate.sha256,
            expected_size=candidate.byte_size,
        )
        if candidate.rights_decision_id is None:
            raise RuntimeError("Export permission passed without a rights decision")
        return AuthorizedPortableByte(
            asset_id=candidate.asset_id,
            stored_object_id=candidate.stored_object_id,
            rights_decision_id=candidate.rights_decision_id,
            format=candidate.asset_format,
            media_type=candidate.media_type,
            sha256=candidate.sha256,
            byte_size=candidate.byte_size,
            source_path=source_path,
        )

    async def copy_for_export(
        self,
        *,
        principal_id: UUID,
        library_entry_id: UUID,
        asset_id: UUID,
        staging_root: Path,
    ) -> PortableByteCopy:
        authorized = await self.authorize_export(
            principal_id=principal_id,
            library_entry_id=library_entry_id,
            asset_id=asset_id,
        )
        root = await asyncio.to_thread(self._prepare_staging_root, staging_root)
        destination = root / authorized.sha256

        if await asyncio.to_thread(destination.exists):
            if await asyncio.to_thread(destination.is_symlink):
                raise BytePortabilityIntegrityError(
                    "staging_object_unsafe",
                    "Existing portability staging object must not be a symbolic link.",
                )
            await self._verify_path(
                destination,
                expected_sha256=authorized.sha256,
                expected_size=authorized.byte_size,
            )
        else:
            temp_path = await asyncio.to_thread(
                self._copy_to_temporary_file,
                authorized.source_path,
                root,
            )
            try:
                await self._verify_path(
                    temp_path,
                    expected_sha256=authorized.sha256,
                    expected_size=authorized.byte_size,
                )
                await asyncio.to_thread(os.replace, temp_path, destination)
            finally:
                with suppress(FileNotFoundError):
                    await asyncio.to_thread(temp_path.unlink)

        return PortableByteCopy(
            asset_id=authorized.asset_id,
            rights_decision_id=authorized.rights_decision_id,
            format=authorized.format,
            media_type=authorized.media_type,
            sha256=authorized.sha256,
            byte_size=authorized.byte_size,
            path=destination,
        )

    @staticmethod
    def _require_export_permission(candidate: BytePortabilityCandidate) -> None:
        if candidate.rights_decision_id is None:
            raise BytePortabilityDenied(
                "rights_missing",
                "No destination-local canonical rights decision authorizes byte export.",
            )
        if candidate.permissions.get("export") is not True:
            raise BytePortabilityDenied(
                "rights_export_denied",
                "The latest destination-local canonical rights decision does not allow export.",
            )

    @staticmethod
    def _require_consistent_metadata(candidate: BytePortabilityCandidate) -> None:
        if (
            candidate.asset_byte_size is not None
            and candidate.asset_byte_size != candidate.byte_size
        ):
            raise BytePortabilityIntegrityError(
                "stored_object_metadata_conflict",
                "Asset byte size disagrees with the canonical stored object.",
            )

    async def _verify_path(
        self,
        path: Path,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> None:
        try:
            actual_sha256, actual_size = await asyncio.to_thread(self._fingerprint, path)
        except OSError as exc:
            raise BytePortabilityIntegrityError(
                "stored_object_unavailable",
                "Portable bytes could not be read as a regular file.",
            ) from exc
        if actual_size != expected_size or actual_sha256.casefold() != expected_sha256.casefold():
            raise BytePortabilityIntegrityError(
                "stored_object_integrity_failed",
                "Stored bytes do not match the canonical SHA-256 and byte size.",
            )

    @staticmethod
    def _prepare_staging_root(root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(f"Portability staging root is not a directory: {root}")
        return resolved

    @staticmethod
    def _copy_to_temporary_file(source: Path, root: Path) -> Path:
        file_descriptor, temp_name = tempfile.mkstemp(
            dir=root,
            prefix=".bukmatika-portable-",
            suffix=".part",
        )
        temp_path = Path(temp_name)
        try:
            with source.open("rb") as input_file, os.fdopen(file_descriptor, "wb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
        except BaseException:
            with suppress(FileNotFoundError):
                temp_path.unlink()
            raise
        return temp_path

    @staticmethod
    def _fingerprint(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
        return digest.hexdigest(), byte_size
