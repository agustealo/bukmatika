import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.library.byte_portability import (
    BytePortabilityDenied,
    BytePortabilityIntegrityError,
    LibraryBytePortabilityService,
)
from bukmatika.persistence.models import (
    Acquisition,
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    RightsDecision,
    StoredObject,
    Work,
)


@dataclass(frozen=True, slots=True)
class _SeededAsset:
    principal_id: UUID
    library_entry_id: UUID
    asset_id: UUID
    rights_decision_id: UUID | None
    source_path: Path
    payload: bytes
    sha256: str
    store: LocalObjectStore
    work_id: UUID
    edition_id: UUID


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with session.begin_nested():
            yield session

    return scope


async def _seed_asset(
    session: AsyncSession,
    tmp_path: Path,
    *,
    permissions: dict[str, bool] | None,
    edition_scoped: bool = True,
) -> _SeededAsset:
    token = uuid4().hex
    payload = f"portable-bytes-{token}".encode()
    sha256 = hashlib.sha256(payload).hexdigest()
    store = LocalObjectStore(tmp_path / f"store-{token}")
    incoming = tmp_path / f"incoming-{token}.part"
    incoming.write_bytes(payload)
    stored_file = await store.commit(incoming, sha256=sha256, format_name="PDF")

    principal = Principal(kind="local", external_subject=f"byte-portability-{token}")
    work = Work(canonical_title=f"Portable {token}", normalized_title=f"portable {token}")
    stored = StoredObject(
        sha256=sha256,
        storage_key=stored_file.storage_key,
        byte_size=len(payload),
        media_type="application/pdf",
    )
    session.add_all([principal, work, stored])
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
        stored_object_id=stored.id,
        byte_size=len(payload),
    )
    session.add(asset)
    await session.flush()

    rights: RightsDecision | None = None
    if permissions is not None:
        rights = RightsDecision(
            subject_type="asset",
            subject_id=asset.id,
            rights_state="public_domain" if permissions.get("export") else "open_license",
            jurisdiction="US",
            policy_version="rights-us-v1",
            permissions=permissions,
            reason="Test rights decision",
            evaluated_at=datetime.now(UTC),
        )
        session.add(rights)
        await session.flush()

    library_entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id if edition_scoped else None,
        status="saved",
    )
    acquisition = Acquisition(
        asset_id=asset.id,
        rights_decision_id=rights.id if rights is not None else None,
        stored_object_id=stored.id,
        status="stored",
        remote_url=asset.remote_url or "https://example.invalid/fallback.pdf",
        expected_format="PDF",
        bytes_received=len(payload),
        sha256=sha256,
        media_type="application/pdf",
    )
    session.add_all([library_entry, acquisition])
    await session.flush()

    return _SeededAsset(
        principal_id=principal.id,
        library_entry_id=library_entry.id,
        asset_id=asset.id,
        rights_decision_id=rights.id if rights is not None else None,
        source_path=stored_file.path,
        payload=payload,
        sha256=sha256,
        store=store,
        work_id=work.id,
        edition_id=edition.id,
    )


def _service(session: AsyncSession, seeded: _SeededAsset) -> LibraryBytePortabilityService:
    return LibraryBytePortabilityService(
        path_resolver=seeded.store,
        session_scope_factory=_scope(session),
    )


async def test_public_domain_owned_asset_copies_verified_bytes(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )

    copied = await _service(session, seeded).copy_for_export(
        principal_id=seeded.principal_id,
        library_entry_id=seeded.library_entry_id,
        asset_id=seeded.asset_id,
        staging_root=tmp_path / "portable-stage",
    )

    assert copied.sha256 == seeded.sha256
    assert copied.byte_size == len(seeded.payload)
    assert copied.rights_decision_id == seeded.rights_decision_id
    assert copied.path.name == seeded.sha256
    assert copied.path.read_bytes() == seeded.payload
    assert copied.path != seeded.source_path


async def test_retain_permission_does_not_imply_export_permission(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": False, "share": False},
    )

    with pytest.raises(BytePortabilityDenied) as denied:
        await _service(session, seeded).copy_for_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
            staging_root=tmp_path / "denied-stage",
        )

    assert denied.value.code == "rights_export_denied"
    assert not (tmp_path / "denied-stage").exists()


async def test_missing_local_rights_decision_fails_closed(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(session, tmp_path, permissions=None)

    with pytest.raises(BytePortabilityDenied) as denied:
        await _service(session, seeded).authorize_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
        )

    assert denied.value.code == "rights_missing"


async def test_latest_local_rights_decision_controls_export(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )
    session.add(
        RightsDecision(
            subject_type="asset",
            subject_id=seeded.asset_id,
            rights_state="restricted",
            jurisdiction="US",
            policy_version="rights-us-v2",
            permissions={"retain": False, "export": False, "share": False},
            reason="Newer policy denies export",
            evaluated_at=datetime.now(UTC) + timedelta(seconds=1),
        )
    )
    await session.flush()

    with pytest.raises(BytePortabilityDenied) as denied:
        await _service(session, seeded).authorize_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
        )

    assert denied.value.code == "rights_export_denied"


async def test_cross_principal_asset_export_is_not_resolvable(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )
    outsider = Principal(kind="local", external_subject=f"outsider-{uuid4().hex}")
    session.add(outsider)
    await session.flush()

    with pytest.raises(BytePortabilityDenied) as denied:
        await _service(session, seeded).authorize_export(
            principal_id=outsider.id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
        )

    assert denied.value.code == "asset_unavailable"


async def test_edition_owned_entry_cannot_export_sibling_edition_asset(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )
    sibling_payload = b"sibling-edition-bytes"
    sibling_sha = hashlib.sha256(sibling_payload).hexdigest()
    sibling_incoming = tmp_path / "sibling.part"
    sibling_incoming.write_bytes(sibling_payload)
    sibling_file = await seeded.store.commit(
        sibling_incoming,
        sha256=sibling_sha,
        format_name="PDF",
    )
    sibling_stored = StoredObject(
        sha256=sibling_sha,
        storage_key=sibling_file.storage_key,
        byte_size=len(sibling_payload),
        media_type="application/pdf",
    )
    sibling_edition = Edition(
        work_id=seeded.work_id,
        title="Sibling Edition",
        language="en",
    )
    session.add_all([sibling_stored, sibling_edition])
    await session.flush()
    sibling_asset = Asset(
        edition_id=sibling_edition.id,
        format="PDF",
        media_type="application/pdf",
        remote_url=f"https://example.invalid/{uuid4().hex}.pdf",
        stored_object_id=sibling_stored.id,
        byte_size=len(sibling_payload),
    )
    session.add(sibling_asset)
    await session.flush()
    sibling_rights = RightsDecision(
        subject_type="asset",
        subject_id=sibling_asset.id,
        rights_state="public_domain",
        jurisdiction="US",
        policy_version="rights-us-v1",
        permissions={"retain": True, "export": True, "share": True},
        reason="Sibling is exportable but not owned through this edition entry",
        evaluated_at=datetime.now(UTC),
    )
    session.add(sibling_rights)
    await session.flush()
    session.add(
        Acquisition(
            asset_id=sibling_asset.id,
            rights_decision_id=sibling_rights.id,
            stored_object_id=sibling_stored.id,
            status="stored",
            remote_url=sibling_asset.remote_url or "https://example.invalid/sibling.pdf",
            expected_format="PDF",
            bytes_received=len(sibling_payload),
            sha256=sibling_sha,
            media_type="application/pdf",
        )
    )
    await session.flush()

    with pytest.raises(BytePortabilityDenied) as denied:
        await _service(session, seeded).authorize_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=sibling_asset.id,
        )

    assert denied.value.code == "asset_unavailable"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("expected_format", "EPUB"),
        ("bytes_received", 1),
        ("sha256", "0" * 64),
        ("media_type", "application/epub+zip"),
    ],
)
async def test_contradictory_acquisition_metadata_fails_closed(
    session: AsyncSession,
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )
    acquisition = await session.scalar(
        select(Acquisition).where(Acquisition.asset_id == seeded.asset_id)
    )
    assert acquisition is not None
    setattr(acquisition, field, bad_value)
    await session.flush()

    with pytest.raises(BytePortabilityIntegrityError) as failed:
        await _service(session, seeded).authorize_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
        )

    assert failed.value.code == "acquisition_metadata_conflict"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("byte_size", 1),
        ("media_type", "application/epub+zip"),
    ],
)
async def test_contradictory_asset_metadata_fails_closed(
    session: AsyncSession,
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )
    asset = await session.scalar(select(Asset).where(Asset.id == seeded.asset_id))
    assert asset is not None
    setattr(asset, field, bad_value)
    await session.flush()

    with pytest.raises(BytePortabilityIntegrityError) as failed:
        await _service(session, seeded).authorize_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
        )

    assert failed.value.code == "stored_object_metadata_conflict"


async def test_tampered_canonical_object_fails_integrity_check(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    seeded = await _seed_asset(
        session,
        tmp_path,
        permissions={"retain": True, "export": True, "share": True},
    )
    seeded.source_path.write_bytes(b"tampered-canonical-object")

    with pytest.raises(BytePortabilityIntegrityError) as failed:
        await _service(session, seeded).authorize_export(
            principal_id=seeded.principal_id,
            library_entry_id=seeded.library_entry_id,
            asset_id=seeded.asset_id,
        )

    assert failed.value.code == "stored_object_integrity_failed"
