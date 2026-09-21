import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.domain import AcquisitionResponse, AcquisitionStatus
from bukmatika.acquisition.downloader import (
    DownloadCancelled,
    DownloadTooLarge,
    RemoteDownloadError,
    SafeDownloader,
)
from bukmatika.acquisition.network import UnsafeRemoteURL
from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.acquisition.verification import FormatVerificationError, verify_download
from bukmatika.config import Settings
from bukmatika.domain import RightsEvidence, RightsState
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition import AcquisitionRepository
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.persistence.models import RightsEvidenceRecord
from bukmatika.rights import RightsEngine

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class AssetNotFound(LookupError):
    pass


class AcquisitionDenied(PermissionError):
    def __init__(self, acquisition_id: UUID, rights_state: RightsState, reason: str) -> None:
        super().__init__(reason)
        self.acquisition_id = acquisition_id
        self.rights_state = rights_state
        self.reason = reason


class AcquisitionCancelled(RuntimeError):
    def __init__(self, acquisition_id: UUID) -> None:
        super().__init__("Acquisition was cancelled")
        self.acquisition_id = acquisition_id


class AcquisitionExecutionError(RuntimeError):
    def __init__(self, error_code: str, detail: str) -> None:
        super().__init__(detail)
        self.error_code = error_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class _AuthorizedAttempt:
    acquisition_id: UUID
    asset_id: UUID
    remote_url: str
    expected_format: str
    rights_state: RightsState


class AcquisitionService:
    """Coordinate rights, hostile-network retrieval, verification, and durable storage."""

    _policy_version = "rights-us-v1"
    _jurisdiction = "US"

    def __init__(
        self,
        downloader: SafeDownloader,
        storage: LocalObjectStore,
        settings: Settings,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._downloader = downloader
        self._storage = storage
        self._settings = settings
        self._rights = RightsEngine()
        self._session_scope = session_scope_factory

    async def acquire(self, asset_id: UUID) -> AcquisitionResponse:
        authorized = await self._authorize(asset_id)
        if isinstance(authorized, AcquisitionResponse):
            return authorized

        temp_path = await self._storage.create_temp_path(authorized.acquisition_id)

        async def cancellation_probe() -> bool:
            return await self._is_cancel_requested(authorized.acquisition_id)

        try:
            result = await self._downloader.download(
                authorized.remote_url,
                temp_path,
                cancellation_probe=cancellation_probe,
            )
        except DownloadCancelled as exc:
            await self._storage.discard(temp_path)
            await self._record_cancelled(authorized)
            raise AcquisitionCancelled(authorized.acquisition_id) from exc
        except UnsafeRemoteURL as exc:
            await self._storage.discard(temp_path)
            await self._record_failure(
                authorized,
                error_code="UNSAFE_REMOTE_URL",
                detail=str(exc),
            )
            raise AcquisitionExecutionError("UNSAFE_REMOTE_URL", str(exc)) from exc
        except DownloadTooLarge as exc:
            await self._storage.discard(temp_path)
            await self._record_failure(
                authorized,
                error_code="ASSET_TOO_LARGE",
                detail=str(exc),
            )
            raise AcquisitionExecutionError("ASSET_TOO_LARGE", str(exc)) from exc
        except (RemoteDownloadError, httpx.HTTPError) as exc:
            await self._storage.discard(temp_path)
            await self._record_failure(
                authorized,
                error_code="REMOTE_DOWNLOAD_FAILED",
                detail=str(exc),
            )
            raise AcquisitionExecutionError("REMOTE_DOWNLOAD_FAILED", str(exc)) from exc

        await self._raise_if_cancelled(authorized, result.temp_path)
        async with self._session_scope() as database_session:
            await AcquisitionRepository(database_session).mark_verifying(
                authorized.acquisition_id,
                bytes_received=result.byte_size,
                sha256=result.sha256,
                media_type=result.media_type,
                redirect_count=result.redirect_count,
            )

        try:
            await asyncio.to_thread(
                verify_download,
                result.temp_path,
                expected_format=authorized.expected_format,
                media_type=result.media_type,
                archive_max_members=self._settings.archive_max_members,
                archive_max_uncompressed_bytes=self._settings.archive_max_uncompressed_bytes,
                archive_max_compression_ratio=self._settings.archive_max_compression_ratio,
            )
        except FormatVerificationError as exc:
            quarantine_key = await self._storage.quarantine(
                result.temp_path,
                authorized.acquisition_id,
            )
            detail = f"{exc}; quarantine={quarantine_key}"
            async with self._session_scope() as database_session:
                repository = AcquisitionRepository(database_session)
                await repository.mark_quarantined(
                    authorized.acquisition_id,
                    error_code="FORMAT_VERIFICATION_FAILED",
                    error_detail=detail,
                )
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.ACQUISITION_QUARANTINED,
                    entity_type="asset",
                    entity_id=authorized.asset_id,
                    context={
                        "acquisition_id": str(authorized.acquisition_id),
                        "error_code": "FORMAT_VERIFICATION_FAILED",
                    },
                )
            raise AcquisitionExecutionError("FORMAT_VERIFICATION_FAILED", str(exc)) from exc

        await self._raise_if_cancelled(authorized, result.temp_path)
        try:
            stored_file = await self._storage.commit(
                result.temp_path,
                sha256=result.sha256,
                format_name=authorized.expected_format,
            )
        except OSError as exc:
            await self._storage.discard(result.temp_path)
            await self._record_failure(
                authorized,
                error_code="STORAGE_FAILED",
                detail=str(exc),
            )
            raise AcquisitionExecutionError("STORAGE_FAILED", str(exc)) from exc

        async with self._session_scope() as database_session:
            repository = AcquisitionRepository(database_session)
            stored_object = await repository.upsert_stored_object(
                sha256=result.sha256,
                storage_key=stored_file.storage_key,
                byte_size=result.byte_size,
                media_type=result.media_type,
            )
            acquisition = await repository.mark_stored(
                authorized.acquisition_id,
                asset_id=authorized.asset_id,
                stored_object_id=stored_object.id,
                byte_size=result.byte_size,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACQUISITION_STORED,
                entity_type="asset",
                entity_id=authorized.asset_id,
                context={
                    "acquisition_id": str(authorized.acquisition_id),
                    "sha256": result.sha256,
                    "byte_size": result.byte_size,
                    "storage_key": stored_file.storage_key,
                },
            )
        return AcquisitionResponse(
            acquisition_id=acquisition.id,
            asset_id=authorized.asset_id,
            status=AcquisitionStatus.STORED,
            rights_state=authorized.rights_state.value,
            sha256=result.sha256,
            byte_size=result.byte_size,
            media_type=result.media_type,
            storage_key=stored_file.storage_key,
        )

    async def _authorize(self, asset_id: UUID) -> _AuthorizedAttempt | AcquisitionResponse:
        denied: AcquisitionDenied | None = None
        authorized: _AuthorizedAttempt | None = None
        async with self._session_scope() as database_session:
            repository = AcquisitionRepository(database_session)
            asset = await repository.get_asset(asset_id)
            if asset is None:
                raise AssetNotFound(f"Asset {asset_id} does not exist")
            acquisition = await repository.create_or_get_acquisition(asset)

            if acquisition.status == AcquisitionStatus.STORED.value:
                if acquisition.stored_object_id is None:
                    raise RuntimeError("Stored acquisition has no stored object")
                stored = await repository.get_stored_object(acquisition.stored_object_id)
                if stored is None:
                    raise RuntimeError("Stored acquisition references missing stored object")
                return AcquisitionResponse(
                    acquisition_id=acquisition.id,
                    asset_id=asset.id,
                    status=AcquisitionStatus.STORED,
                    sha256=stored.sha256,
                    byte_size=stored.byte_size,
                    media_type=stored.media_type,
                    storage_key=stored.storage_key,
                )

            acquisition = await repository.begin_attempt(acquisition.id)
            evidence_records = await repository.rights_evidence_for_asset(asset.id)
            evidence = [self._to_domain_evidence(record) for record in evidence_records]
            decision = self._rights.decide(evidence)
            permissions = self._permissions(decision.state)
            stored_decision = await repository.record_rights_decision(
                asset_id=asset.id,
                rights_state=decision.state.value,
                permissions=permissions,
                reason=decision.reason,
                evidence_ids=[record.id for record in evidence_records],
                jurisdiction=self._jurisdiction,
                policy_version=self._policy_version,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACQUISITION_ATTEMPT_STARTED,
                entity_type="asset",
                entity_id=asset.id,
                context={
                    "acquisition_id": str(acquisition.id),
                    "attempt_count": acquisition.attempt_count,
                    "rights_state": decision.state.value,
                },
            )

            if not decision.unattended_acquisition_allowed or not permissions["download"]:
                await repository.mark_failed(
                    acquisition.id,
                    error_code="RIGHTS_DENIED",
                    error_detail=decision.reason,
                    rights_decision_id=stored_decision.id,
                )
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.ACQUISITION_DENIED,
                    entity_type="asset",
                    entity_id=asset.id,
                    context={
                        "acquisition_id": str(acquisition.id),
                        "rights_state": decision.state.value,
                    },
                )
                denied = AcquisitionDenied(acquisition.id, decision.state, decision.reason)
            else:
                await repository.mark_downloading(acquisition.id, stored_decision.id)
                if asset.remote_url is None:
                    raise RuntimeError("Acquirable asset has no remote URL")
                authorized = _AuthorizedAttempt(
                    acquisition_id=acquisition.id,
                    asset_id=asset.id,
                    remote_url=asset.remote_url,
                    expected_format=asset.format,
                    rights_state=decision.state,
                )

        if denied is not None:
            raise denied
        if authorized is None:
            raise RuntimeError("Acquisition authorization produced no outcome")
        return authorized

    async def _is_cancel_requested(self, acquisition_id: UUID) -> bool:
        async with self._session_scope() as database_session:
            return await AcquisitionRepository(database_session).is_cancel_requested(acquisition_id)

    async def _raise_if_cancelled(self, authorized: _AuthorizedAttempt, path: Path) -> None:
        if not await self._is_cancel_requested(authorized.acquisition_id):
            return
        await self._storage.discard(path)
        await self._record_cancelled(authorized)
        raise AcquisitionCancelled(authorized.acquisition_id)

    async def _record_cancelled(self, authorized: _AuthorizedAttempt) -> None:
        async with self._session_scope() as database_session:
            repository = AcquisitionRepository(database_session)
            await repository.mark_cancelled(authorized.acquisition_id)
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACQUISITION_CANCELLED,
                entity_type="asset",
                entity_id=authorized.asset_id,
                context={"acquisition_id": str(authorized.acquisition_id)},
            )

    async def _record_failure(
        self,
        authorized: _AuthorizedAttempt,
        *,
        error_code: str,
        detail: str,
    ) -> None:
        async with self._session_scope() as database_session:
            repository = AcquisitionRepository(database_session)
            await repository.mark_failed(
                authorized.acquisition_id,
                error_code=error_code,
                error_detail=detail,
            )
            await InteractionEventRepository(database_session).record(
                SemanticEventType.ACQUISITION_FAILED,
                entity_type="asset",
                entity_id=authorized.asset_id,
                context={
                    "acquisition_id": str(authorized.acquisition_id),
                    "error_code": error_code,
                },
            )

    @staticmethod
    def _to_domain_evidence(record: RightsEvidenceRecord) -> RightsEvidence:
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

    @staticmethod
    def _permissions(state: RightsState) -> dict[str, bool]:
        if state is RightsState.PUBLIC_DOMAIN:
            return {
                "discover": True,
                "display_metadata": True,
                "download": True,
                "retain": True,
                "process": True,
                "ocr": True,
                "export": True,
                "share": True,
            }
        if state in {RightsState.OPEN_LICENSE, RightsState.AUTHORIZED_DOWNLOAD}:
            return {
                "discover": True,
                "display_metadata": True,
                "download": True,
                "retain": True,
                "process": True,
                "ocr": True,
                "export": False,
                "share": False,
            }
        return {
            "discover": True,
            "display_metadata": True,
            "download": False,
            "retain": False,
            "process": False,
            "ocr": False,
            "export": False,
            "share": False,
        }
