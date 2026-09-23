import hashlib
import stat
import warnings
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from bukmatika.config import Settings
from bukmatika.library.portability_bundle import (
    LibraryPortabilityBundleIndex,
    LibraryPortabilityBundleService,
    PortabilityBundleError,
    PortableBundleByte,
    PortableBundleOmission,
)
from bukmatika.library.portability_domain import (
    LibraryPortabilityExportResponse,
    PortableAssetManifest,
    PortableBytePolicy,
    PortableEditionIdentity,
    PortableLibraryEntry,
    PortableWorkIdentity,
)
from bukmatika.main import app


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        storage_root=tmp_path / "storage",
        acquisition_worker_enabled=False,
        **overrides,
    )


def _empty_manifest() -> LibraryPortabilityExportResponse:
    return LibraryPortabilityExportResponse(
        exported_at=datetime(2026, 9, 23, tzinfo=UTC),
        entries=[],
        collections=[],
        tags=[],
        smart_shelves=[],
    )


def _manifest_with_asset(payload: bytes) -> tuple[
    LibraryPortabilityExportResponse,
    UUID,
    UUID,
    str,
]:
    source_work_id = uuid4()
    source_edition_id = uuid4()
    source_library_entry_id = uuid4()
    source_asset_id = uuid4()
    sha256 = hashlib.sha256(payload).hexdigest()
    edition = PortableEditionIdentity(
        source_edition_id=source_edition_id,
        title="Portable Test Edition",
        language="en",
        publication_year=2026,
        publisher="Bukmatika Test",
        edition_statement=None,
        identifiers=[],
        sources=[],
    )
    asset = PortableAssetManifest(
        source_asset_id=source_asset_id,
        edition=edition,
        format="TXT",
        media_type="text/plain",
        byte_size=len(payload),
        content_sha256=sha256,
        identifiers=[],
        sources=[],
        document=None,
        rights=None,
        byte_policy=PortableBytePolicy(
            policy_export_allowed=True,
            policy_share_allowed=True,
        ),
    )
    entry = PortableLibraryEntry(
        source_library_entry_id=source_library_entry_id,
        status="saved",
        created_at=datetime(2026, 9, 23, tzinfo=UTC),
        updated_at=datetime(2026, 9, 23, tzinfo=UTC),
        work=PortableWorkIdentity(
            source_work_id=source_work_id,
            canonical_title="Portable Test Work",
            authors=["Test Author"],
            subjects=[],
            identifiers=[],
            sources=[],
        ),
        edition=edition,
        assets=[asset],
        reading_states=[],
        collection_ids=[],
        tag_ids=[],
    )
    return (
        LibraryPortabilityExportResponse(
            exported_at=datetime(2026, 9, 23, tzinfo=UTC),
            entries=[entry],
            collections=[],
            tags=[],
            smart_shelves=[],
        ),
        source_library_entry_id,
        source_asset_id,
        sha256,
    )


def _write_metadata(
    archive: zipfile.ZipFile,
    manifest: LibraryPortabilityExportResponse,
    index: LibraryPortabilityBundleIndex,
) -> None:
    archive.writestr("manifest.json", manifest.model_dump_json())
    archive.writestr("bundle.json", index.model_dump_json())


@pytest.mark.asyncio
async def test_bundle_rejects_duplicate_archive_member_names(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.bukmatika"
    manifest = _empty_manifest()
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
            _write_metadata(archive, manifest, index)
            archive.writestr("manifest.json", manifest.model_dump_json())

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_duplicate_member"


@pytest.mark.asyncio
async def test_bundle_rejects_symlink_member_before_extraction(tmp_path: Path) -> None:
    path = tmp_path / "symlink.bukmatika"
    manifest = _empty_manifest()
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    link = zipfile.ZipInfo("bytes/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)
        archive.writestr(link, b"../../escape")

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_symlink_member"


@pytest.mark.asyncio
async def test_bundle_rejects_member_count_over_configured_limit(tmp_path: Path) -> None:
    path = tmp_path / "members.bukmatika"
    manifest = _empty_manifest()
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)

    service = LibraryPortabilityBundleService(
        settings=_settings(tmp_path, archive_max_members=1)
    )
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_member_limit"


@pytest.mark.asyncio
async def test_bundle_rejects_compression_ratio_abuse(tmp_path: Path) -> None:
    path = tmp_path / "ratio.bukmatika"
    manifest = _empty_manifest()
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
        _write_metadata(archive, manifest, index)
        archive.writestr("bytes/unindexed", b"A" * 16_384)

    service = LibraryPortabilityBundleService(
        settings=_settings(
            tmp_path,
            archive_max_uncompressed_bytes=1_000_000,
            archive_max_compression_ratio=2,
        )
    )
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_compression_ratio"


@pytest.mark.asyncio
async def test_bundle_rejects_missing_required_metadata_member(tmp_path: Path) -> None:
    path = tmp_path / "missing-metadata.bukmatika"
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("bundle.json", index.model_dump_json())

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_metadata_missing"


@pytest.mark.asyncio
async def test_bundle_rejects_manifest_asset_without_disposition(tmp_path: Path) -> None:
    payload = b"portable"
    manifest, _, _, _ = _manifest_with_asset(payload)
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    path = tmp_path / "missing-disposition.bukmatika"
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_asset_coverage"


@pytest.mark.asyncio
async def test_bundle_rejects_asset_marked_both_included_and_omitted(tmp_path: Path) -> None:
    payload = b"portable"
    manifest, entry_id, asset_id, sha256 = _manifest_with_asset(payload)
    item = PortableBundleByte(
        source_library_entry_id=entry_id,
        source_asset_id=asset_id,
        member_name=f"bytes/{sha256}",
        format="TXT",
        media_type="text/plain",
        sha256=sha256,
        byte_size=len(payload),
    )
    index = LibraryPortabilityBundleIndex(
        bytes=[item],
        omissions=[
            PortableBundleOmission(
                source_library_entry_id=entry_id,
                source_asset_id=asset_id,
                code="forged_omission",
                detail="The same asset cannot be both included and omitted.",
            )
        ],
    )
    path = tmp_path / "double-disposition.bukmatika"
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)
        archive.writestr(item.member_name, payload)

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_asset_coverage"


@pytest.mark.asyncio
async def test_bundle_rejects_index_manifest_identity_disagreement(tmp_path: Path) -> None:
    payload = b"portable"
    manifest, entry_id, asset_id, sha256 = _manifest_with_asset(payload)
    item = PortableBundleByte(
        source_library_entry_id=entry_id,
        source_asset_id=asset_id,
        member_name=f"bytes/{sha256}",
        format="EPUB",
        media_type="application/epub+zip",
        sha256=sha256,
        byte_size=len(payload),
    )
    index = LibraryPortabilityBundleIndex(bytes=[item], omissions=[])
    path = tmp_path / "identity-disagreement.bukmatika"
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)
        archive.writestr(item.member_name, payload)

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_manifest_byte_disagreement"


@pytest.mark.asyncio
async def test_bundle_rejects_payload_hash_tampering(tmp_path: Path) -> None:
    payload = b"original"
    tampered = b"tampered"
    assert len(payload) == len(tampered)
    manifest, entry_id, asset_id, sha256 = _manifest_with_asset(payload)
    item = PortableBundleByte(
        source_library_entry_id=entry_id,
        source_asset_id=asset_id,
        member_name=f"bytes/{sha256}",
        format="TXT",
        media_type="text/plain",
        sha256=sha256,
        byte_size=len(payload),
    )
    index = LibraryPortabilityBundleIndex(bytes=[item], omissions=[])
    path = tmp_path / "tampered.bukmatika"
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)
        archive.writestr(item.member_name, tampered)

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_byte_identity_mismatch"


@pytest.mark.asyncio
async def test_bundle_rejects_symlink_input_file(tmp_path: Path) -> None:
    target = tmp_path / "target.bukmatika"
    manifest = _empty_manifest()
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    with zipfile.ZipFile(target, mode="x", compression=zipfile.ZIP_STORED) as archive:
        _write_metadata(archive, manifest, index)
    link = tmp_path / "link.bukmatika"
    link.symlink_to(target)

    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))
    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=link)

    assert raised.value.code == "bundle_unavailable"


def test_consumer_portability_file_routes_are_public_contracts() -> None:
    paths = app.openapi()["paths"]

    assert "get" in paths["/v1/library/export/file"]
    assert "post" in paths["/v1/library/import/file/plan"]
    assert "post" in paths["/v1/library/import/file/apply"]
