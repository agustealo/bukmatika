from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.library.local_import_identity import local_import_source_key
from bukmatika.persistence.identity_models import PrincipalSession
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    SourceObservation,
    SourceRecord,
    SourceRecordLink,
    StoredObject,
    Work,
)
from bukmatika.persistence.personalization_models import (
    PreferenceClaim,
    PreferenceClaimEvidence,
    UserModel,
)
from bukmatika.persistence.privacy_models import PrivacyErasureObject
from bukmatika.privacy.service import AccountPrivacyService


def _scope(
    session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject=str(uuid4()))
    session.add(principal)
    await session.flush()
    return principal


async def _local_import(
    session: AsyncSession,
    *,
    principal: Principal,
    stored_object: StoredObject,
    title: str,
    filename: str,
) -> tuple[Work, SourceRecord]:
    work = Work(canonical_title=title, normalized_title=title.casefold())
    session.add(work)
    await session.flush()
    edition = Edition(work_id=work.id, title=title)
    session.add(edition)
    await session.flush()
    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        stored_object_id=stored_object.id,
        byte_size=stored_object.byte_size,
    )
    session.add(asset)
    await session.flush()
    session.add(
        LibraryEntry(
            principal_id=principal.id,
            work_id=work.id,
            edition_id=edition.id,
            status="saved",
        )
    )
    source = SourceRecord(
        provider="local-import",
        provider_record_id=local_import_source_key(principal.id, stored_object.sha256),
        canonical_url=f"bukmatika://local-import/{uuid4().hex}",
    )
    session.add(source)
    await session.flush()
    for entity_type, entity_id in (
        ("work", work.id),
        ("edition", edition.id),
        ("asset", asset.id),
    ):
        session.add(
            SourceRecordLink(
                source_record_id=source.id,
                entity_type=entity_type,
                entity_id=entity_id,
                relationship="describes",
            )
        )
    session.add(
        SourceObservation(
            source_record_id=source.id,
            payload_sha256=uuid4().hex + uuid4().hex,
            payload={"source_kind": "user_local_import", "filename": filename, "title": title},
            parser_version="local-import-v1",
        )
    )
    await session.flush()
    return work, source


def _stored_path(root: Path, sha256: str) -> tuple[str, Path]:
    storage_key = f"objects/{sha256[:2]}/{sha256[2:4]}/{sha256}"
    return storage_key, root / storage_key


@pytest.mark.asyncio
async def test_account_export_includes_owned_data_without_session_or_worker_secrets(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    other = await _principal(session)
    session.add(UserModel(principal_id=principal.id))
    now = datetime.now(UTC)
    session.add(
        PrincipalSession(
            principal_id=principal.id,
            token_sha256="a" * 64,
            expires_at=now + timedelta(days=1),
            last_seen_at=now,
        )
    )
    session.add(
        InteractionEvent(
            principal_id=principal.id,
            event_type="research.search",
            context={"query": "private research question"},
        )
    )
    session.add(
        InteractionEvent(
            principal_id=other.id,
            event_type="research.search",
            context={"query": "other principal"},
        )
    )
    sha256 = "1" * 64
    storage_key, path = _stored_path(tmp_path, sha256)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"private local book")
    stored = StoredObject(
        sha256=sha256,
        storage_key=storage_key,
        byte_size=path.stat().st_size,
        media_type="text/plain",
    )
    session.add(stored)
    await session.flush()
    await _local_import(
        session,
        principal=principal,
        stored_object=stored,
        title="Private Notes",
        filename="private-notes.txt",
    )

    service = AccountPrivacyService(
        storage=LocalObjectStore(tmp_path),
        session_scope_factory=_scope(session),
    )
    exported = await service.export(principal_id=principal.id)

    assert exported.principal.principal_id == principal.id
    assert exported.principal.external_subject == principal.external_subject
    assert len(exported.library.entries) == 1
    session_record = next(
        record
        for record in exported.operational_records
        if record.record_type == "principal_sessions"
    )
    assert "token_sha256" not in session_record.data
    events = [
        record
        for record in exported.operational_records
        if record.record_type == "interaction_events"
    ]
    assert len(events) == 1
    assert events[0].data["context"] == {"query": "private research question"}
    observation = next(
        record
        for record in exported.operational_records
        if record.record_type == "local_import_observation"
    )
    assert observation.data["payload"] == {
        "source_kind": "user_local_import",
        "filename": "private-notes.txt",
        "title": "Private Notes",
    }


@pytest.mark.asyncio
async def test_local_import_provenance_stays_bound_to_importing_principal(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner = await _principal(session)
    other = await _principal(session)
    sha256 = "4" * 64
    storage_key, path = _stored_path(tmp_path, sha256)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"owner-private provenance")
    stored = StoredObject(
        sha256=sha256,
        storage_key=storage_key,
        byte_size=path.stat().st_size,
        media_type="text/plain",
    )
    session.add(stored)
    await session.flush()
    work, source = await _local_import(
        session,
        principal=owner,
        stored_object=stored,
        title="Owner Import",
        filename="owner-secret-name.txt",
    )
    edition = await session.scalar(select(Edition).where(Edition.work_id == work.id))
    assert edition is not None
    session.add(
        LibraryEntry(
            principal_id=other.id,
            work_id=work.id,
            edition_id=edition.id,
            status="saved",
        )
    )
    await session.flush()

    service = AccountPrivacyService(
        storage=LocalObjectStore(tmp_path),
        session_scope_factory=_scope(session),
    )
    owner_export = await service.export(principal_id=owner.id)
    other_export = await service.export(principal_id=other.id)

    assert any(
        record.record_type == "local_import_observation"
        for record in owner_export.operational_records
    )
    assert all(
        record.record_type not in {"local_import_source", "local_import_observation"}
        for record in other_export.operational_records
    )

    await service.delete_account(principal_id=other.id)
    await session.flush()
    assert await session.get(SourceRecord, source.id) is not None
    assert await session.get(Work, work.id) is not None
    assert path.exists()


@pytest.mark.asyncio
async def test_account_delete_erases_private_local_import_events_and_bytes(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    other = await _principal(session)
    session.add(
        InteractionEvent(
            principal_id=principal.id,
            event_type="library.local_import.stored",
            context={"private": True},
        )
    )
    session.add(
        InteractionEvent(
            principal_id=other.id,
            event_type="library.opened",
            context={"other": True},
        )
    )
    sha256 = "2" * 64
    storage_key, path = _stored_path(tmp_path, sha256)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"erase me")
    stored = StoredObject(
        sha256=sha256,
        storage_key=storage_key,
        byte_size=path.stat().st_size,
        media_type="text/plain",
    )
    session.add(stored)
    await session.flush()
    private_work, source = await _local_import(
        session,
        principal=principal,
        stored_object=stored,
        title="Erase Me",
        filename="erase-me.txt",
    )

    service = AccountPrivacyService(
        storage=LocalObjectStore(tmp_path),
        session_scope_factory=_scope(session),
    )
    result = await service.delete_account(principal_id=principal.id)
    await session.flush()

    assert result.principal_deleted is True
    assert result.interaction_events_deleted == 1
    assert result.private_local_imports_deleted == 1
    assert result.storage_objects_queued == 1
    assert result.storage_objects_deleted_immediately == 1
    assert not path.exists()
    assert await session.get(Principal, principal.id) is None
    assert await session.get(Work, private_work.id) is None
    assert await session.get(SourceRecord, source.id) is None
    assert await session.scalar(
        select(InteractionEvent.id).where(InteractionEvent.principal_id == principal.id)
    ) is None
    assert await session.get(Principal, other.id) is not None
    assert await session.scalar(
        select(InteractionEvent.id).where(InteractionEvent.principal_id == other.id)
    ) is not None
    queued = await session.scalar(
        select(PrivacyErasureObject).where(PrivacyErasureObject.storage_key == storage_key)
    )
    assert queued is not None
    assert queued.status == "deleted"


@pytest.mark.asyncio
async def test_account_delete_removes_preference_evidence_before_interaction_history(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    user_model = UserModel(principal_id=principal.id)
    session.add(user_model)
    await session.flush()

    interaction = InteractionEvent(
        principal_id=principal.id,
        event_type="research.search",
        context={"query": "private inferred preference evidence"},
    )
    session.add(interaction)
    await session.flush()

    claim = PreferenceClaim(
        user_model_id=user_model.id,
        principal_id=principal.id,
        key="research.topic",
        value={"topic": "private"},
        source="inferred",
        confidence=0.8,
        evidence_count=1,
    )
    session.add(claim)
    await session.flush()
    session.add(
        PreferenceClaimEvidence(
            preference_claim_id=claim.id,
            interaction_event_id=interaction.id,
        )
    )
    await session.flush()

    service = AccountPrivacyService(
        storage=LocalObjectStore(tmp_path),
        session_scope_factory=_scope(session),
    )
    result = await service.delete_account(principal_id=principal.id)
    await session.flush()

    assert result.principal_deleted is True
    assert result.interaction_events_deleted == 1
    assert await session.get(Principal, principal.id) is None
    assert await session.get(InteractionEvent, interaction.id) is None
    assert await session.scalar(
        select(PreferenceClaimEvidence.interaction_event_id).where(
            PreferenceClaimEvidence.interaction_event_id == interaction.id
        )
    ) is None


@pytest.mark.asyncio
async def test_account_delete_preserves_content_addressed_bytes_still_referenced_elsewhere(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    other = await _principal(session)
    sha256 = "3" * 64
    storage_key, path = _stored_path(tmp_path, sha256)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"shared content")
    stored = StoredObject(
        sha256=sha256,
        storage_key=storage_key,
        byte_size=path.stat().st_size,
        media_type="text/plain",
    )
    session.add(stored)
    await session.flush()
    private_work, _ = await _local_import(
        session,
        principal=principal,
        stored_object=stored,
        title="Private Copy",
        filename="private-copy.txt",
    )
    surviving_work, _ = await _local_import(
        session,
        principal=other,
        stored_object=stored,
        title="Other Copy",
        filename="other-copy.txt",
    )

    service = AccountPrivacyService(
        storage=LocalObjectStore(tmp_path),
        session_scope_factory=_scope(session),
    )
    result = await service.delete_account(principal_id=principal.id)
    await session.flush()

    assert result.private_local_imports_deleted == 1
    assert result.storage_objects_queued == 0
    assert path.exists()
    assert await session.get(StoredObject, stored.id) is not None
    assert await session.get(Work, private_work.id) is None
    assert await session.get(Work, surviving_work.id) is not None
    assert await session.get(Principal, other.id) is not None


def test_all_direct_principal_foreign_keys_are_cascade_owned_except_interaction_history() -> None:
    from bukmatika.persistence.models import Base

    exceptions = {("interaction_events", "SET NULL")}
    observed_exceptions: set[tuple[str, str]] = set()
    for table in Base.metadata.tables.values():
        if "principal_id" not in table.c:
            continue
        for foreign_key in table.c.principal_id.foreign_keys:
            if foreign_key.target_fullname != "principals.id":
                continue
            action = (foreign_key.ondelete or "").upper()
            if action != "CASCADE":
                observed_exceptions.add((table.name, action))
    assert observed_exceptions == exceptions
