from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.persistence import session_scope
from bukmatika.persistence.acquisition_request_models import AcquisitionPolicy, AcquisitionRequest
from bukmatika.persistence.action_models import ActionExecutionReceipt
from bukmatika.persistence.delegation_control_models import AIDelegationConsent
from bukmatika.persistence.delegation_models import (
    AIDelegation,
    AIDelegationApproval,
    AIDelegationAttempt,
)
from bukmatika.persistence.delegation_result_models import AIDelegationAttemptResult
from bukmatika.persistence.document_models import Document, DocumentProcessingState
from bukmatika.persistence.identity_models import PrincipalSession
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Contributor,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    RightsDecision,
    RightsEvidenceSubject,
    SourceObservation,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Work,
    WorkContributor,
)
from bukmatika.persistence.personalization_models import ActionApproval
from bukmatika.persistence.privacy_models import PrivacyErasureObject
from bukmatika.personalization.portability import PersonalizationPortabilityService
from bukmatika.privacy.domain import (
    AccountDeleteResponse,
    AccountPrivacyExportResponse,
    OperationalRecordExport,
    PrincipalExport,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class PrincipalNotFound(RuntimeError):
    pass


class AccountPrivacyService:
    """Whole-account portability and erasure authority.

    Shared catalog truth is preserved. Principal-private local-import provenance is erased and its
    physical bytes are queued for durable deletion only when no surviving canonical record still
    references the same content-addressed object.
    """

    def __init__(
        self,
        *,
        storage: LocalObjectStore,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._storage = storage
        self._session_scope = session_scope_factory
        self._library = LibraryPortabilityService(session_scope_factory=session_scope_factory)
        self._personalization = PersonalizationPortabilityService(
            session_scope_factory=session_scope_factory
        )

    async def export(self, *, principal_id: UUID) -> AccountPrivacyExportResponse:
        library = await self._library.export(principal_id=principal_id)
        personalization = await self._personalization.export(principal_id=principal_id)
        async with self._session_scope() as database_session:
            principal = await database_session.get(Principal, principal_id)
            if principal is None:
                raise PrincipalNotFound("Principal does not exist")
            operational_records = await self._operational_records(database_session, principal_id)
            operational_records.extend(
                await self._local_import_records(database_session, principal_id)
            )
            operational_records.sort(key=lambda item: (item.record_type, item.record_id))
            return AccountPrivacyExportResponse(
                exported_at=datetime.now(UTC),
                principal=PrincipalExport(
                    principal_id=principal.id,
                    kind=principal.kind,
                    external_subject=principal.external_subject,
                    created_at=principal.created_at,
                    updated_at=principal.updated_at,
                ),
                library=library,
                personalization=personalization,
                operational_records=operational_records,
            )

    async def delete_account(self, *, principal_id: UUID) -> AccountDeleteResponse:
        deleted_at = datetime.now(UTC)
        interaction_events_deleted = 0
        private_local_imports_deleted = 0
        storage_objects_queued = 0

        async with self._session_scope() as database_session:
            principal = await database_session.scalar(
                select(Principal).where(Principal.id == principal_id).with_for_update()
            )
            if principal is None:
                raise PrincipalNotFound("Principal does not exist")

            local_sources, private_work_ids = await self._local_import_scope(
                database_session, principal_id
            )
            source_ids = [source.id for source in local_sources]

            stored_objects: list[StoredObject] = []
            contributor_ids: set[UUID] = set()
            if private_work_ids:
                asset_rows = (
                    await database_session.execute(
                        select(Asset.id, Asset.stored_object_id)
                        .join(Edition, Edition.id == Asset.edition_id)
                        .where(Edition.work_id.in_(private_work_ids))
                    )
                ).all()
                asset_ids = [asset_id for asset_id, _ in asset_rows]
                stored_object_ids = {
                    stored_object_id
                    for _, stored_object_id in asset_rows
                    if stored_object_id is not None
                }
                contributor_ids = set(
                    await database_session.scalars(
                        select(WorkContributor.contributor_id).where(
                            WorkContributor.work_id.in_(private_work_ids)
                        )
                    )
                )
                if asset_ids:
                    await database_session.execute(
                        delete(RightsDecision).where(
                            RightsDecision.subject_type == "asset",
                            RightsDecision.subject_id.in_(asset_ids),
                        )
                    )
                    await database_session.execute(
                        delete(RightsEvidenceSubject).where(
                            RightsEvidenceSubject.subject_type == "asset",
                            RightsEvidenceSubject.subject_id.in_(asset_ids),
                        )
                    )
                if stored_object_ids:
                    stored_objects = list(
                        await database_session.scalars(
                            select(StoredObject)
                            .where(StoredObject.id.in_(stored_object_ids))
                            .with_for_update()
                        )
                    )

            if source_ids:
                await database_session.execute(
                    delete(SourceRecord).where(SourceRecord.id.in_(source_ids))
                )

            if private_work_ids:
                await database_session.execute(delete(Work).where(Work.id.in_(private_work_ids)))
                private_local_imports_deleted = len(private_work_ids)
                await database_session.flush()

            for contributor_id in contributor_ids:
                still_used = await database_session.scalar(
                    select(exists().where(WorkContributor.contributor_id == contributor_id))
                )
                if not still_used:
                    contributor = await database_session.get(Contributor, contributor_id)
                    if contributor is not None:
                        await database_session.delete(contributor)

            for stored_object in stored_objects:
                if await self._stored_object_is_referenced(database_session, stored_object.id):
                    continue
                await self._queue_storage_erasure(database_session, stored_object.storage_key)
                await database_session.delete(stored_object)
                storage_objects_queued += 1

            interaction_events_deleted = int(
                await database_session.scalar(
                    select(func.count())
                    .select_from(InteractionEvent)
                    .where(InteractionEvent.principal_id == principal_id)
                )
                or 0
            )
            await database_session.execute(
                delete(InteractionEvent).where(InteractionEvent.principal_id == principal_id)
            )
            await database_session.delete(principal)

        cleanup = await self.cleanup_pending_storage()
        return AccountDeleteResponse(
            deleted_at=deleted_at,
            interaction_events_deleted=interaction_events_deleted,
            private_local_imports_deleted=private_local_imports_deleted,
            storage_objects_queued=storage_objects_queued,
            storage_objects_deleted_immediately=cleanup["deleted"],
            storage_objects_pending_retry=cleanup["pending"],
        )

    async def cleanup_pending_storage(self, *, limit: int = 100) -> dict[str, int]:
        deleted_count = 0
        pending_count = 0
        async with self._session_scope() as database_session:
            rows = list(
                await database_session.scalars(
                    select(PrivacyErasureObject)
                    .where(PrivacyErasureObject.status == "pending")
                    .order_by(PrivacyErasureObject.queued_at, PrivacyErasureObject.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.attempt_count += 1
                row.last_attempt_at = datetime.now(UTC)
                live_again = await database_session.scalar(
                    select(exists().where(StoredObject.storage_key == row.storage_key))
                )
                if live_again:
                    row.status = "retained"
                    row.last_error_code = None
                    continue
                try:
                    path = await self._storage.resolve_path(row.storage_key)
                    await self._storage.discard(path)
                except FileNotFoundError:
                    pass
                except (OSError, ValueError):
                    row.last_error_code = "STORAGE_DELETE_FAILED"
                    pending_count += 1
                    continue
                row.status = "deleted"
                row.last_error_code = None
                row.deleted_at = datetime.now(UTC)
                deleted_count += 1
        return {"deleted": deleted_count, "pending": pending_count}

    async def _queue_storage_erasure(
        self,
        database_session: AsyncSession,
        storage_key: str,
    ) -> None:
        queued = await database_session.scalar(
            select(PrivacyErasureObject)
            .where(PrivacyErasureObject.storage_key == storage_key)
            .with_for_update()
        )
        if queued is None:
            database_session.add(PrivacyErasureObject(storage_key=storage_key, status="pending"))
            return
        queued.status = "pending"
        queued.last_error_code = None
        queued.deleted_at = None

    async def _operational_records(
        self,
        database_session: AsyncSession,
        principal_id: UUID,
    ) -> list[OperationalRecordExport]:
        records: list[OperationalRecordExport] = []
        models: tuple[tuple[type[Any], frozenset[str]], ...] = (
            (PrincipalSession, frozenset({"token_sha256"})),
            (AcquisitionPolicy, frozenset()),
            (AcquisitionRequest, frozenset()),
            (InteractionEvent, frozenset()),
            (ActionApproval, frozenset()),
            (ActionExecutionReceipt, frozenset()),
            (AIDelegationConsent, frozenset()),
            (AIDelegation, frozenset()),
            (AIDelegationApproval, frozenset()),
            (AIDelegationAttempt, frozenset({"claim_token"})),
            (AIDelegationAttemptResult, frozenset()),
        )
        for model, omitted in models:
            rows = list(
                await database_session.scalars(
                    select(model).where(model.principal_id == principal_id)
                )
            )
            for row in rows:
                records.append(self._record(model.__tablename__, row, omitted=omitted))
        return records

    async def _local_import_records(
        self,
        database_session: AsyncSession,
        principal_id: UUID,
    ) -> list[OperationalRecordExport]:
        sources, _ = await self._local_import_scope(database_session, principal_id)
        if not sources:
            return []
        source_ids = [source.id for source in sources]
        observations = list(
            await database_session.scalars(
                select(SourceObservation).where(SourceObservation.source_record_id.in_(source_ids))
            )
        )
        records = [self._record("local_import_source", source) for source in sources]
        records.extend(
            self._record("local_import_observation", observation) for observation in observations
        )
        return records

    async def _local_import_scope(
        self,
        database_session: AsyncSession,
        principal_id: UUID,
    ) -> tuple[list[SourceRecord], set[UUID]]:
        rows = (
            await database_session.execute(
                select(SourceRecord, LibraryEntry.work_id)
                .join(SourceRecordLink, SourceRecordLink.source_record_id == SourceRecord.id)
                .join(
                    LibraryEntry,
                    (SourceRecordLink.entity_type == "work")
                    & (SourceRecordLink.entity_id == LibraryEntry.work_id),
                )
                .where(
                    SourceRecord.provider == "local-import",
                    LibraryEntry.principal_id == principal_id,
                )
                .distinct()
            )
        ).all()
        sources_by_id = {source.id: source for source, _ in rows}
        work_ids = {work_id for _, work_id in rows}
        if not work_ids:
            return list(sources_by_id.values()), set()
        shared_work_ids = set(
            await database_session.scalars(
                select(LibraryEntry.work_id)
                .where(
                    LibraryEntry.work_id.in_(work_ids),
                    LibraryEntry.principal_id != principal_id,
                )
                .distinct()
            )
        )
        return list(sources_by_id.values()), work_ids - shared_work_ids

    async def _stored_object_is_referenced(
        self,
        database_session: AsyncSession,
        stored_object_id: UUID,
    ) -> bool:
        checks = (
            select(exists().where(Asset.stored_object_id == stored_object_id)),
            select(exists().where(Document.stored_object_id == stored_object_id)),
            select(exists().where(DocumentProcessingState.stored_object_id == stored_object_id)),
            select(exists().where(Acquisition.stored_object_id == stored_object_id)),
        )
        for statement in checks:
            if await database_session.scalar(statement):
                return True
        return False

    @classmethod
    def _record(
        cls,
        record_type: str,
        row: object,
        *,
        omitted: frozenset[str] = frozenset(),
    ) -> OperationalRecordExport:
        mapped_row = cast(Any, row)
        table = mapped_row.__table__
        data: dict[str, JsonValue] = {}
        record_id = ""
        for column in table.columns:
            if column.name in omitted or column.name == "principal_id":
                continue
            value = getattr(mapped_row, column.name)
            if column.name == "id":
                record_id = str(value)
            data[column.name] = cls._json_value(value)
        if not record_id:
            principal_value = getattr(mapped_row, "principal_id", None)
            record_id = str(principal_value) if principal_value is not None else record_type
        return OperationalRecordExport(
            record_type=record_type,
            record_id=record_id,
            data=data,
        )

    @classmethod
    def _json_value(cls, value: object) -> JsonValue:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, dict):
            return cast(
                JsonValue,
                {str(key): cls._json_value(item) for key, item in value.items()},
            )
        if isinstance(value, list | tuple):
            return cast(JsonValue, [cls._json_value(item) for item in value])
        return str(value)


__all__ = ["AccountPrivacyService", "PrincipalNotFound"]
