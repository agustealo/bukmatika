import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bukmatika.config import Settings
from bukmatika.library.portability_bundle import (
    LibraryPortabilityBundleIndex,
    LibraryPortabilityBundleService,
    PortabilityBundleError,
)
from bukmatika.library.portability_domain import LibraryPortabilityExportResponse


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        storage_root=tmp_path / "storage",
        acquisition_worker_enabled=False,
        **overrides,
    )


def _manifest() -> LibraryPortabilityExportResponse:
    return LibraryPortabilityExportResponse(
        exported_at=datetime(2026, 9, 23, tzinfo=UTC),
        entries=[],
        collections=[],
        tags=[],
        smart_shelves=[],
    )


def _write_bundle(path: Path, *, extra_members: dict[str, bytes] | None = None) -> None:
    manifest = _manifest()
    index = LibraryPortabilityBundleIndex(bytes=[], omissions=[])
    with zipfile.ZipFile(path, mode="x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest.model_dump_json())
        archive.writestr("bundle.json", index.model_dump_json())
        for name, content in (extra_members or {}).items():
            archive.writestr(name, content)


@pytest.mark.asyncio
async def test_bundle_inspection_accepts_bounded_metadata_only_package(tmp_path: Path) -> None:
    path = tmp_path / "library.bukmatika"
    _write_bundle(path)
    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))

    inspection = await service.inspect_bundle(bundle_path=path)
    try:
        assert inspection.manifest.export_kind == "bukmatika-library-manifest"
        assert inspection.index.bundle_kind == "bukmatika-library-bundle"
        assert inspection.byte_paths == {}
    finally:
        inspection.cleanup()


@pytest.mark.asyncio
async def test_bundle_inspection_rejects_path_traversal_before_extraction(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.bukmatika"
    _write_bundle(path, extra_members={"../escape.txt": b"nope"})
    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))

    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_member_path_invalid"
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
async def test_bundle_inspection_rejects_unindexed_members(tmp_path: Path) -> None:
    path = tmp_path / "unindexed.bukmatika"
    _write_bundle(path, extra_members={"bytes/not-authorized": b"hidden payload"})
    service = LibraryPortabilityBundleService(settings=_settings(tmp_path))

    with pytest.raises(PortabilityBundleError) as raised:
        await service.inspect_bundle(bundle_path=path)

    assert raised.value.code == "bundle_unknown_member"


@pytest.mark.asyncio
async def test_bundle_upload_is_stream_bounded(tmp_path: Path) -> None:
    service = LibraryPortabilityBundleService(
        settings=_settings(tmp_path, archive_max_uncompressed_bytes=8)
    )

    async def chunks() -> AsyncIterator[bytes]:
        yield b"12345"
        yield b"6789"

    with pytest.raises(PortabilityBundleError) as raised:
        await service.receive_bundle(chunks())

    assert raised.value.code == "bundle_too_large"
