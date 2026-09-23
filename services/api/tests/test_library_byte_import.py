import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import Settings
from bukmatika.library.byte_import import (
    LibraryPortableByteImportService,
    PortableByteImportConflict,
    PortableByteImportDenied,
    PortableByteImportIntegrityError,
)
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableAssetManifest,
    PortableBytePolicy,
    PortableEditionIdentity,
    PortableIdentifier,
    PortableLibraryEntry,
    PortableRightsSnapshot,
    PortableWorkIdentity,
)
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Edition,
    Identifier,
    LibraryEntry,
    Principal,
    RightsDecision,
    StoredObject,
    Work,
)


@dataclass(frozen=True, slots=True)
class _SeededPortableTarget:
    principal_id: UUID
    library_entry_id: UUID
    asset_id: UUID
    source_library_entry_id: UUID
    source_asset_id: UUID
    manifest: LibraryPortabilityExportResponse
    source_path: Path
    payload: bytes
    sha256: str
    store: LocalObjectStore


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


def _identifier(entity_type: str, entity_id: UUID, scheme: str, value: str) -> Identifier:
    return Identifier(
        entity_type=entity_type,
        entity_id=entity_id,
        scheme=scheme,
        value=value,
        normalized_value=value,
    )


async def _seed_target(
    session: AsyncSession,
    tmp_path: Path,
    *,
    retain_allowed: bool = True,
    payload: bytes | None = None,
    source_rights_allow: bool = True,
) -> _SeededPortableTarget:
    token = uuid4().hex
    content = payload or b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    sha256 = hashlib.sha256(content).hexdigest()
    principal = Principal(kind="local", external_subject=f"portable-byte-{token}")
    work = Work(canonical_title=f"Portable Work {token}", normalized_title=f"portable work {token}")
    session.add_all([principal, work])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Portable Edition {token}",
        language="en",
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.invalid/{token}.pdf",
        byte_size=len(content),
    )
    library_entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add_all([asset, library_entry])
    await session.flush()

    work_key = f"work-{token}"
    edition_key = f"edition-{token}"
    asset_key = f"asset-{token}"
    session.add_all(
        [
            _identifier("work", work.id, "portable-work", work_key),
            _identifier("edition", edition.id, "portable-edition", edition_key),
            _identifier("asset", asset.id, "portable-asset", asset_key),
        ]
    )
    rights = RightsDecision(
        subject_type="asset",
        subject_id=asset.id,
        rights_state="public_domain" if retain_allowed else "restricted",
        jurisdiction="US",
        policy_version="rights-us-v1",
        permissions={
            "discover": True,
            "display_metadata": True,
            "download": retain_allowed,
            "retain": retain_allowed,
            "process": retain_allowed,
            "ocr": retain_allowed,
            "export": retain_allowed,
            "share": retain_allowed,
        },
        reason="Destination-local rights decision",
        evaluated_at=datetime.now(UTC),
    )
    session.add(rights)
    await session.flush()

    source_work_id = uuid4()
    source_edition_id = uuid4()
    source_asset_id = uuid4()
    source_library_entry_id = uuid4()
    portable_edition = PortableEditionIdentity(
        source_edition_id=source_edition_id,
        title=edition.title,
        language=edition.language,
        publication_year=None,
        publisher=None,
        edition_statement=None,
        identifiers=[PortableIdentifier(scheme="portable-edition", value=edition_key)],
        sources=[],
    )
    portable_asset = PortableAssetManifest(
        source_asset_id=source_asset_id,
        edition=portable_edition,
        format="PDF",
        media_type="application/pdf",
        byte_size=len(content),
        content_sha256=sha256,
        identifiers=[PortableIdentifier(scheme="portable-asset", value=asset_key)],
        sources=[],
        document=None,
        rights=PortableRightsSnapshot(
            rights_state="public_domain" if source_rights_allow else "restricted",
            jurisdiction="US",
            policy_version="source-policy",
            permissions={
                "retain": source_rights_allow,
                "export": source_rights_allow,
                "share": source_rights_allow,
            },
            reason="Portable source snapshot only",
            evaluated_at=datetime.now(UTC),
            evidence=[],
        ),
        byte_policy=PortableBytePolicy(
            policy_export_allowed=source_rights_allow,
            policy_share_allowed=source_rights_allow,
        ),
    )
    manifest = LibraryPortabilityExportResponse(
        exported_at=datetime.now(UTC),
        entries=[
            PortableLibraryEntry(
                source_library_entry_id=source_library_entry_id,
                status="saved",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                work=PortableWorkIdentity(
                    source_work_id=source_work_id,
                    canonical_title=work.canonical_title,
                    authors=[],
                    subjects=[],
                    identifiers=[PortableIdentifier(scheme="portable-work", value=work_key)],
                    sources=[],
                ),
                edition=portable_edition,
                assets=[portable_asset],
                reading_states=[],
                collection_ids=[],
                tag_ids=[],
            )
        ],
        collections=[],
        tags=[],
        smart_shelves=[],
    )
    source_path = tmp_path / f"portable-{token}.pdf"
    source_path.write_bytes(content)
    return _SeededPortableTarget(
        principal_id=principal.id,
        library_entry_id=library_entry.id,
        asset_id=asset.id,
        source_library_entry_id=source_library_entry_id,
        source_asset_id=source_asset_id,
        manifest=manifest,
        source_path=source_path,
        payload=content,
        sha256=sha256,
        store=LocalObjectStore(tmp_path / f"store-{token}"),
    )


def _service(
    session: AsyncSession,
    seeded: _SeededPortableTarget,
) -> LibraryPortableByteImportService:
    return LibraryPortableByteImportService(
        storage=seeded.store,
        settings=Settings(),
        session_scope_factory=_scope(session),
    )


async def test_portable_byte_import_commits_through_canonical_acquisition(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path)

    result = await _service(session, seeded).import_byte(
        principal_id=seeded.principal_id,
        manifest=seeded.manifest,
        source_library_entry_id=seeded.source_library_entry_id,
        source_asset_id=seeded.source_asset_id,
        source_path=seeded.source_path,
    )

    assert result.asset_id == seeded.asset_id
    assert result.sha256 == seeded.sha256
    assert result.byte_size == len(seeded.payload)
    assert result.idempotent is False
    asset = await session.get(Asset, seeded.asset_id)
    acquisition = await session.scalar(
        select(Acquisition).where(Acquisition.asset_id == seeded.asset_id)
    )
    assert asset is not None
    assert acquisition is not None
    assert acquisition.status == "stored"
    assert acquisition.rights_decision_id == result.rights_decision_id
    assert acquisition.stored_object_id == result.stored_object_id
    assert asset.stored_object_id == result.stored_object_id
    stored = await session.get(StoredObject, result.stored_object_id)
    assert stored is not None
    assert stored.sha256 == seeded.sha256
    path = await seeded.store.resolve_path(stored.storage_key)
    assert path.read_bytes() == seeded.payload
    assert seeded.source_path.read_bytes() == seeded.payload


async def test_portable_byte_import_is_idempotent_for_same_canonical_content(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path)
    service = _service(session, seeded)

    first = await service.import_byte(
        principal_id=seeded.principal_id,
        manifest=seeded.manifest,
        source_library_entry_id=seeded.source_library_entry_id,
        source_asset_id=seeded.source_asset_id,
        source_path=seeded.source_path,
    )
    second = await service.import_byte(
        principal_id=seeded.principal_id,
        manifest=seeded.manifest,
        source_library_entry_id=seeded.source_library_entry_id,
        source_asset_id=seeded.source_asset_id,
        source_path=seeded.source_path,
    )

    assert second.idempotent is True
    assert second.stored_object_id == first.stored_object_id
    assert second.acquisition_id == first.acquisition_id
    assert await session.scalar(select(func.count()).select_from(StoredObject)) == 1
    assert await session.scalar(select(func.count()).select_from(Acquisition)) == 1


async def test_destination_rights_denial_overrides_permissive_source_snapshot(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(
        session,
        tmp_path,
        retain_allowed=False,
        source_rights_allow=True,
    )

    with pytest.raises(PortableByteImportDenied) as denied:
        await _service(session, seeded).import_byte(
            principal_id=seeded.principal_id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert denied.value.code == "portable_rights_retain_denied"
    asset = await session.get(Asset, seeded.asset_id)
    assert asset is not None
    assert asset.stored_object_id is None
    assert await session.scalar(select(func.count()).select_from(Acquisition)) == 0


async def test_latest_destination_rights_decision_controls_retention(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path, retain_allowed=True)
    session.add(
        RightsDecision(
            subject_type="asset",
            subject_id=seeded.asset_id,
            rights_state="restricted",
            jurisdiction="US",
            policy_version="rights-us-v2",
            permissions={"retain": False, "export": False, "share": False},
            reason="Retention revoked locally",
            evaluated_at=datetime.now(UTC) + timedelta(seconds=1),
        )
    )
    await session.flush()

    with pytest.raises(PortableByteImportDenied) as denied:
        await _service(session, seeded).import_byte(
            principal_id=seeded.principal_id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert denied.value.code == "portable_rights_retain_denied"


async def test_portable_hash_mismatch_never_reaches_canonical_storage(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path)
    seeded.source_path.write_bytes(seeded.payload + b"tampered")

    with pytest.raises(PortableByteImportIntegrityError) as failed:
        await _service(session, seeded).import_byte(
            principal_id=seeded.principal_id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert failed.value.code == "portable_content_identity_mismatch"
    asset = await session.get(Asset, seeded.asset_id)
    assert asset is not None
    assert asset.stored_object_id is None
    assert await session.scalar(select(func.count()).select_from(StoredObject)) == 0


async def test_portable_payload_must_pass_canonical_format_verification(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(
        session,
        tmp_path,
        payload=b"this has the declared hash but is not a PDF",
    )

    with pytest.raises(PortableByteImportIntegrityError) as failed:
        await _service(session, seeded).import_byte(
            principal_id=seeded.principal_id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert failed.value.code == "portable_format_verification_failed"
    assert await session.scalar(select(func.count()).select_from(StoredObject)) == 0


async def test_portable_byte_import_cannot_cross_principal_ownership(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path)
    outsider = Principal(kind="local", external_subject=f"outsider-{uuid4().hex}")
    session.add(outsider)
    await session.flush()

    with pytest.raises(PortableByteImportDenied) as denied:
        await _service(session, seeded).import_byte(
            principal_id=outsider.id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert denied.value.code == "portable_destination_unowned"
    assert await session.scalar(select(func.count()).select_from(Acquisition)) == 0


async def test_ambiguous_asset_metadata_fallback_fails_closed(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path)
    manifest_asset = seeded.manifest.entries[0].assets[0]
    seeded.manifest.entries[0].assets[0] = manifest_asset.model_copy(update={"identifiers": []})
    destination = await session.get(Asset, seeded.asset_id)
    assert destination is not None
    identifier = await session.scalar(
        select(Identifier).where(
            Identifier.entity_type == "asset",
            Identifier.entity_id == seeded.asset_id,
        )
    )
    assert identifier is not None
    await session.delete(identifier)
    session.add(
        Asset(
            edition_id=destination.edition_id,
            format=destination.format,
            media_type=destination.media_type,
            remote_url=f"https://example.invalid/{uuid4().hex}.pdf",
            byte_size=destination.byte_size,
        )
    )
    await session.flush()

    with pytest.raises(PortableByteImportConflict) as conflict:
        await _service(session, seeded).import_byte(
            principal_id=seeded.principal_id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert conflict.value.code == "portable_asset_identity_ambiguous"


async def test_active_canonical_acquisition_blocks_portable_ingest(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_target(session, tmp_path)
    asset = await session.get(Asset, seeded.asset_id)
    assert asset is not None
    session.add(
        Acquisition(
            asset_id=asset.id,
            status="verifying",
            remote_url=asset.remote_url or "https://example.invalid/fallback.pdf",
            expected_format=asset.format,
            attempt_count=1,
            bytes_received=len(seeded.payload),
            sha256=seeded.sha256,
            media_type=asset.media_type,
            redirect_count=0,
        )
    )
    await session.flush()

    with pytest.raises(PortableByteImportConflict) as conflict:
        await _service(session, seeded).import_byte(
            principal_id=seeded.principal_id,
            manifest=seeded.manifest,
            source_library_entry_id=seeded.source_library_entry_id,
            source_asset_id=seeded.source_asset_id,
            source_path=seeded.source_path,
        )

    assert conflict.value.code == "portable_acquisition_active"
