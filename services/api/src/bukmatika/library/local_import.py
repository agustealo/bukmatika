import asyncio
import hashlib
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, suppress
from pathlib import Path
from typing import Any, BinaryIO, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import AcquisitionObjectStore
from bukmatika.acquisition.verification import FormatVerificationError, verify_download
from bukmatika.config import Settings
from bukmatika.normalization import normalize_text
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition import AcquisitionRepository
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.library import LibraryRepository
from bukmatika.persistence.local_import import LocalImportIdentityConflict, LocalImportRepository
from bukmatika.rights import RightsEngine

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

LOCAL_IMPORT_MEDIA_TYPE = "application/vnd.bukmatika.local-import"
_LOCAL_IMPORT_PROVIDER = "local-import"
_LOCAL_IMPORT_PARSER_VERSION = "local-import-v1"
_LOCAL_IMPORT_METADATA_MAX_BYTES = 8_192

_FORMATS: dict[str, tuple[str, str]] = {
    ".pdf": ("PDF", "application/pdf"),
    ".epub": ("EPUB", "application/epub+zip"),
    ".txt": ("TXT", "text/plain"),
    ".html": ("HTML", "text/html"),
    ".htm": ("HTML", "text/html"),
    ".xhtml": ("HTML", "application/xhtml+xml"),
    ".docx": (
        "DOCX",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}


class LocalImportError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class LocalImportTooLarge(LocalImportError):
    pass


class LocalImportEnvelopeMetadata(BaseModel):
    schema_version: Literal[1] = 1
    filename: str = Field(min_length=1, max_length=255)
    title: str | None = Field(default=None, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    reported_media_type: str | None = Field(default=None, max_length=255)


class LocalImportResponse(BaseModel):
    library_entry_id: UUID
    work_id: UUID
    edition_id: UUID
    asset_id: UUID
    document_id: UUID | None
    title: str
    authors: list[str]
    format: str
    media_type: str
    sha256: str
    byte_size: int = Field(ge=1)
    idempotent: bool
    processing_required: bool
    rights_state: Literal["unknown"] = "unknown"
    private_retention_only: Literal[True] = True


class _ReceivedLocalFile(BaseModel):
    metadata: LocalImportEnvelopeMetadata
    format_name: str
    media_type: str
    sha256: str
    byte_size: int


class LocalLibraryImportService:
    """Ingest user-supplied book bytes into canonical storage and library ownership.

    Local imports deliberately bypass network acquisition because no remote retrieval occurs.
    They still use canonical hostile-file verification, content-addressed storage, catalog/source
    provenance, RightsDecision persistence, and principal-scoped LibraryEntry ownership.
    """

    def __init__(
        self,
        *,
        storage: AcquisitionObjectStore,
        settings: Settings,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._session_scope = session_scope_factory
        self._rights = RightsEngine()

    async def import_stream(
        self,
        *,
        principal_id: UUID,
        stream: AsyncIterator[bytes],
    ) -> LocalImportResponse:
        temp_path = await self._storage.create_temp_path(uuid4())
        try:
            received = await self._receive(stream=stream, temp_path=temp_path)
            await self._verify(temp_path, received)
            return await self._commit(
                principal_id=principal_id,
                temp_path=temp_path,
                received=received,
            )
        finally:
            with suppress(FileNotFoundError):
                await self._storage.discard(temp_path)

    async def _receive(
        self,
        *,
        stream: AsyncIterator[bytes],
        temp_path: Path,
    ) -> _ReceivedLocalFile:
        metadata_buffer = bytearray()
        metadata: LocalImportEnvelopeMetadata | None = None
        digest = hashlib.sha256()
        byte_size = 0
        max_bytes = min(self._settings.acquisition_max_bytes, self._settings.processing_max_bytes)
        handle = await asyncio.to_thread(temp_path.open, "wb")
        try:
            async for chunk in stream:
                if not chunk:
                    continue
                if metadata is None:
                    metadata_buffer.extend(chunk)
                    separator = metadata_buffer.find(b"\n")
                    if separator < 0:
                        if len(metadata_buffer) > _LOCAL_IMPORT_METADATA_MAX_BYTES:
                            raise LocalImportError(
                                "LOCAL_IMPORT_METADATA_TOO_LARGE",
                                "Local import metadata exceeds the supported envelope limit.",
                            )
                        continue
                    metadata_bytes = bytes(metadata_buffer[:separator])
                    payload = bytes(metadata_buffer[separator + 1 :])
                    metadata_buffer.clear()
                    metadata = self._parse_metadata(metadata_bytes)
                    if payload:
                        byte_size = await self._write_payload(
                            handle=handle,
                            chunk=payload,
                            digest=digest,
                            byte_size=byte_size,
                            max_bytes=max_bytes,
                        )
                    continue

                byte_size = await self._write_payload(
                    handle=handle,
                    chunk=chunk,
                    digest=digest,
                    byte_size=byte_size,
                    max_bytes=max_bytes,
                )
        finally:
            await asyncio.to_thread(handle.close)

        if metadata is None:
            raise LocalImportError(
                "LOCAL_IMPORT_METADATA_MISSING",
                "Local import envelope is missing its metadata preamble.",
            )
        if byte_size == 0:
            raise LocalImportError("LOCAL_IMPORT_EMPTY", "Local import contains no book bytes.")

        format_name, media_type = self._format(metadata.filename)
        return _ReceivedLocalFile(
            metadata=metadata,
            format_name=format_name,
            media_type=media_type,
            sha256=digest.hexdigest(),
            byte_size=byte_size,
        )

    async def _write_payload(
        self,
        *,
        handle: BinaryIO,
        chunk: bytes,
        digest: Any,
        byte_size: int,
        max_bytes: int,
    ) -> int:
        next_size = byte_size + len(chunk)
        if next_size > max_bytes:
            raise LocalImportTooLarge(
                "LOCAL_IMPORT_TOO_LARGE",
                f"Local import exceeds the processable byte limit of {max_bytes} bytes.",
            )
        digest.update(chunk)
        await asyncio.to_thread(handle.write, chunk)
        return next_size

    def _parse_metadata(self, payload: bytes) -> LocalImportEnvelopeMetadata:
        if not payload or len(payload) > _LOCAL_IMPORT_METADATA_MAX_BYTES:
            raise LocalImportError(
                "LOCAL_IMPORT_METADATA_INVALID",
                "Local import metadata is missing or exceeds the supported envelope limit.",
            )
        try:
            parsed = LocalImportEnvelopeMetadata.model_validate_json(payload)
        except ValidationError as exc:
            raise LocalImportError(
                "LOCAL_IMPORT_METADATA_INVALID",
                "Local import metadata is invalid.",
            ) from exc

        filename = self._clean_filename(parsed.filename)
        title = self._display_text(parsed.title) if parsed.title is not None else None
        author = self._display_text(parsed.author) if parsed.author is not None else None
        fallback_title = self._display_text(Path(filename).stem.replace("_", " "))
        if title is None:
            title = fallback_title
        if title is None:
            raise LocalImportError(
                "LOCAL_IMPORT_TITLE_REQUIRED",
                "A usable title could not be derived from the selected file.",
            )
        return parsed.model_copy(
            update={
                "filename": filename,
                "title": title,
                "author": author,
                "reported_media_type": self._display_text(parsed.reported_media_type),
            }
        )

    async def _verify(self, path: Path, received: _ReceivedLocalFile) -> None:
        try:
            await asyncio.to_thread(
                verify_download,
                path,
                expected_format=received.format_name,
                media_type=None,
                archive_max_members=self._settings.archive_max_members,
                archive_max_uncompressed_bytes=self._settings.archive_max_uncompressed_bytes,
                archive_max_compression_ratio=self._settings.archive_max_compression_ratio,
            )
        except FormatVerificationError as exc:
            raise LocalImportError(
                "LOCAL_IMPORT_FORMAT_VERIFICATION_FAILED",
                str(exc).replace("Downloaded asset", "Selected file"),
            ) from exc

    async def _commit(
        self,
        *,
        principal_id: UUID,
        temp_path: Path,
        received: _ReceivedLocalFile,
    ) -> LocalImportResponse:
        source_key = self._source_key(principal_id, received.sha256)
        async with self._session_scope() as database_session:
            catalog = CatalogRepository(database_session)
            local_imports = LocalImportRepository(database_session)
            library = LibraryRepository(database_session)
            source = await catalog.upsert_source_record(
                provider=_LOCAL_IMPORT_PROVIDER,
                provider_record_id=source_key,
                canonical_url=f"bukmatika://local-import/{source_key}",
            )
            await local_imports.lock_source(source.id)
            existing = await local_imports.existing_for_source(
                principal_id=principal_id,
                source_record_id=source.id,
            )
            if existing is not None:
                if existing.stored_object.sha256 != received.sha256:
                    raise LocalImportIdentityConflict(
                        "Local import source points at different stored content"
                    )
                document = await local_imports.document_for_asset(existing.asset.id)
                await self._record_event(
                    database_session,
                    principal_id=principal_id,
                    asset_id=existing.asset.id,
                    received=received,
                    idempotent=True,
                )
                return LocalImportResponse(
                    library_entry_id=existing.library_entry.id,
                    work_id=existing.work.id,
                    edition_id=existing.edition.id,
                    asset_id=existing.asset.id,
                    document_id=document.id if document is not None else None,
                    title=existing.work.canonical_title,
                    authors=await library.authors_for_work(existing.work.id),
                    format=existing.asset.format,
                    media_type=existing.asset.media_type or received.media_type,
                    sha256=existing.stored_object.sha256,
                    byte_size=existing.stored_object.byte_size,
                    idempotent=True,
                    processing_required=document is None,
                )

            stored_file = await self._storage.commit(
                temp_path,
                sha256=received.sha256,
                format_name=received.format_name,
            )
            stored_object = await AcquisitionRepository(database_session).upsert_stored_object(
                sha256=received.sha256,
                storage_key=stored_file.storage_key,
                byte_size=received.byte_size,
                media_type=received.media_type,
            )

            title = received.metadata.title
            if title is None:
                raise LocalImportError(
                    "LOCAL_IMPORT_TITLE_REQUIRED",
                    "Local import title is missing.",
                )
            work = await catalog.create_work(title=title, normalized_title=normalize_text(title))
            if received.metadata.author is not None:
                await catalog.add_author(
                    work_id=work.id,
                    display_name=received.metadata.author,
                    normalized_name=normalize_text(received.metadata.author),
                )
            edition = await catalog.create_edition(
                work_id=work.id,
                title=title,
                language=None,
                publication_year=None,
                publisher=None,
            )
            asset = await local_imports.create_asset(
                edition_id=edition.id,
                format_name=received.format_name,
                media_type=received.media_type,
                stored_object_id=stored_object.id,
                byte_size=received.byte_size,
            )

            observation = await catalog.record_source_observation(
                source_record_id=source.id,
                payload={
                    "source_kind": "user_local_import",
                    "filename": received.metadata.filename,
                    "title": title,
                    "author": received.metadata.author,
                    "reported_media_type": received.metadata.reported_media_type,
                    "format": received.format_name,
                    "sha256": received.sha256,
                    "byte_size": received.byte_size,
                },
                parser_version=_LOCAL_IMPORT_PARSER_VERSION,
            )
            for entity_type, entity_id in (
                ("work", work.id),
                ("edition", edition.id),
                ("asset", asset.id),
            ):
                await catalog.link_source_record(
                    source_record_id=source.id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                )
            await catalog.record_assertion(
                entity_type="work",
                entity_id=work.id,
                field_name="canonical_title",
                value=title,
                source_observation_id=observation.id,
                confidence=1.0,
                normalization_method="user_supplied_or_filename",
            )
            await catalog.record_assertion(
                entity_type="edition",
                entity_id=edition.id,
                field_name="title",
                value=title,
                source_observation_id=observation.id,
                confidence=1.0,
                normalization_method="user_supplied_or_filename",
            )
            if received.metadata.author is not None:
                await catalog.record_assertion(
                    entity_type="work",
                    entity_id=work.id,
                    field_name="authors",
                    value=[received.metadata.author],
                    source_observation_id=observation.id,
                    confidence=1.0,
                    normalization_method="user_supplied",
                )

            evidence = await catalog.record_rights_evidence(
                source_observation_id=observation.id,
                state="unknown",
                source="user_local_import",
                basis=(
                    "User explicitly supplied the bytes for private local retention. "
                    "No copyright or license status was inferred from possession."
                ),
                evidence_url=None,
                license_uri=None,
                confidence=1.0,
            )
            await catalog.link_rights_evidence(
                rights_evidence_id=evidence.id,
                subject_type="asset",
                subject_id=asset.id,
            )
            rights = self._rights.decide_user_supplied_local_import()
            await AcquisitionRepository(database_session).record_rights_decision(
                asset_id=asset.id,
                rights_state=rights.state.value,
                permissions=dict(rights.permissions),
                reason=rights.reason,
                evidence_ids=[evidence.id],
                jurisdiction=rights.jurisdiction,
                policy_version=rights.policy_version,
            )
            library_entry = await library.save_edition(principal_id, edition.id)
            await self._record_event(
                database_session,
                principal_id=principal_id,
                asset_id=asset.id,
                received=received,
                idempotent=False,
            )
            return LocalImportResponse(
                library_entry_id=library_entry.id,
                work_id=work.id,
                edition_id=edition.id,
                asset_id=asset.id,
                document_id=None,
                title=work.canonical_title,
                authors=await library.authors_for_work(work.id),
                format=asset.format,
                media_type=asset.media_type or received.media_type,
                sha256=stored_object.sha256,
                byte_size=stored_object.byte_size,
                idempotent=False,
                processing_required=True,
            )

    async def _record_event(
        self,
        database_session: AsyncSession,
        *,
        principal_id: UUID,
        asset_id: UUID,
        received: _ReceivedLocalFile,
        idempotent: bool,
    ) -> None:
        await InteractionEventRepository(database_session).record(
            SemanticEventType.LIBRARY_LOCAL_IMPORT_STORED,
            principal_id=principal_id,
            entity_type="asset",
            entity_id=asset_id,
            context={
                "format": received.format_name,
                "sha256": received.sha256,
                "byte_size": received.byte_size,
                "idempotent": idempotent,
            },
        )

    @staticmethod
    def _source_key(principal_id: UUID, sha256: str) -> str:
        digest = hashlib.sha256()
        digest.update(principal_id.bytes)
        digest.update(bytes.fromhex(sha256))
        return digest.hexdigest()

    @staticmethod
    def _format(filename: str) -> tuple[str, str]:
        value = _FORMATS.get(Path(filename).suffix.casefold())
        if value is None:
            supported = ", ".join(sorted(_FORMATS))
            raise LocalImportError(
                "LOCAL_IMPORT_FORMAT_UNSUPPORTED",
                f"Unsupported local book format. Supported extensions: {supported}.",
            )
        return value

    @staticmethod
    def _clean_filename(value: str) -> str:
        filename = unicodedata.normalize("NFKC", value).strip()
        if (
            not filename
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or "\x00" in filename
            or any(ord(character) < 32 for character in filename)
        ):
            raise LocalImportError(
                "LOCAL_IMPORT_FILENAME_INVALID",
                "Local import filename must be a plain basename without path components.",
            )
        return filename

    @staticmethod
    def _display_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value)
        normalized = " ".join(normalized.split()).strip()
        return normalized or None


__all__ = [
    "LOCAL_IMPORT_MEDIA_TYPE",
    "LocalImportEnvelopeMetadata",
    "LocalImportError",
    "LocalImportResponse",
    "LocalImportTooLarge",
    "LocalLibraryImportService",
]
