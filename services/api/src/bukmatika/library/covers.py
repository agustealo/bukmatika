from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.downloader import RemoteDownloadError, SafeDownloader
from bukmatika.acquisition.network import UnsafeRemoteURL
from bukmatika.domain import DiscoveredCover
from bukmatika.persistence import session_scope
from bukmatika.persistence.library import (
    DossierIdentityConflict,
    DossierNotFound,
    LibraryRepository,
)
from bukmatika.persistence.metadata_provenance import (
    MetadataProvenanceRecord,
    MetadataProvenanceRepository,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class CoverNotFound(LookupError):
    pass


class CoverUnavailable(RuntimeError):
    pass


class CoverInvalid(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CoverMaterialized:
    path: Path
    media_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class _CoverSelection:
    url: str
    kind: str
    confidence: float
    observed_timestamp: float
    provider: str
    assertion_id: UUID

    @property
    def cache_key(self) -> str:
        return f"{self.assertion_id}:{self.url}"


class CoverCache:
    """Disposable local cache for sanitized cover bytes; provenance remains the source of truth."""

    def __init__(self, root: Path) -> None:
        self._root = root / "covers"

    async def existing(self, cache_key: str) -> Path | None:
        path = self._path(cache_key)
        if await asyncio.to_thread(path.is_file):
            return path
        return None

    async def create_temp_path(self, suffix: str) -> Path:
        incoming = self._root / "incoming"
        await asyncio.to_thread(incoming.mkdir, parents=True, exist_ok=True)
        return incoming / f"{uuid4()}.{suffix}"

    async def commit(self, temp_path: Path, cache_key: str) -> Path:
        destination = self._path(cache_key)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        if await asyncio.to_thread(destination.exists):
            await self.discard(temp_path)
        else:
            await asyncio.to_thread(os.replace, temp_path, destination)
        return destination

    async def discard(self, path: Path) -> None:
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return

    def _path(self, cache_key: str) -> Path:
        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self._root / "objects" / digest[:2] / digest[2:4] / f"{digest}.png"


class CoverService:
    """Select provenance-backed cover metadata and materialize safe local image bytes."""

    def __init__(
        self,
        *,
        downloader: SafeDownloader,
        cache: CoverCache,
        max_source_pixels: int,
        max_dimension: int,
        max_cached_bytes: int,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._downloader = downloader
        self._cache = cache
        self._max_source_pixels = max_source_pixels
        self._max_dimension = max_dimension
        self._max_cached_bytes = max_cached_bytes
        self._session_scope = session_scope_factory

    async def cover_for_work(self, *, work_id: UUID) -> CoverMaterialized:
        selections = await self._selections_for_work(work_id)
        return await self._materialize_first_available(selections)

    async def cover_for_source(
        self,
        *,
        provider: str,
        provider_record_id: str,
    ) -> CoverMaterialized:
        async with self._session_scope() as database_session:
            library = LibraryRepository(database_session)
            work_id = await library.resolve_source_work_id(provider, provider_record_id)
            selections = await self._selections(database_session, work_id=work_id)
        return await self._materialize_first_available(selections)

    async def _selections_for_work(self, work_id: UUID) -> list[_CoverSelection]:
        async with self._session_scope() as database_session:
            library = LibraryRepository(database_session)
            if await library.get_work(work_id) is None:
                raise CoverNotFound("Work is not in the canonical catalog")
            return await self._selections(database_session, work_id=work_id)

    async def _selections(
        self,
        database_session: AsyncSession,
        *,
        work_id: UUID,
    ) -> list[_CoverSelection]:
        records = await MetadataProvenanceRepository(database_session).assertions_for_entity(
            entity_type="work",
            entity_id=work_id,
        )
        selections = _cover_selections(records)
        if not selections:
            raise CoverNotFound("No canonical cover metadata is available")
        return selections

    async def _materialize_first_available(
        self,
        selections: list[_CoverSelection],
    ) -> CoverMaterialized:
        failures = 0
        for selection in selections:
            cached = await self._cache.existing(selection.cache_key)
            if cached is not None:
                return CoverMaterialized(path=cached)
            try:
                return await self._materialize(selection)
            except (
                CoverInvalid,
                RemoteDownloadError,
                UnsafeRemoteURL,
                httpx.HTTPError,
                OSError,
            ):
                failures += 1
                continue
        if failures:
            raise CoverUnavailable("Canonical cover candidates could not be materialized")
        raise CoverNotFound("No canonical cover metadata is available")

    async def _materialize(self, selection: _CoverSelection) -> CoverMaterialized:
        download_path = await self._cache.create_temp_path("download")
        encoded_path = await self._cache.create_temp_path("png")
        try:
            await self._downloader.download(selection.url, download_path)
            await asyncio.to_thread(
                _sanitize_cover,
                download_path,
                encoded_path,
                max_source_pixels=self._max_source_pixels,
                max_dimension=self._max_dimension,
                max_cached_bytes=self._max_cached_bytes,
            )
            committed = await self._cache.commit(encoded_path, selection.cache_key)
            return CoverMaterialized(path=committed)
        finally:
            await self._cache.discard(download_path)
            await self._cache.discard(encoded_path)


def _cover_selections(records: list[MetadataProvenanceRecord]) -> list[_CoverSelection]:
    selections: list[_CoverSelection] = []
    for record in records:
        assertion = record.assertion
        if (
            assertion.field_name != "covers"
            or assertion.normalization_method != "bukmatika-cover-normalize-v1"
        ):
            continue
        if not isinstance(assertion.value, list):
            continue
        for item in assertion.value:
            try:
                cover = DiscoveredCover.model_validate(item)
            except ValidationError:
                continue
            selections.append(
                _CoverSelection(
                    url=str(cover.url),
                    kind=cover.kind,
                    confidence=assertion.confidence,
                    observed_timestamp=record.observation.last_observed_at.timestamp(),
                    provider=record.source.provider,
                    assertion_id=assertion.id,
                )
            )
    selections.sort(
        key=lambda item: (
            0 if item.kind == "cover" else 1,
            -item.confidence,
            -item.observed_timestamp,
            item.provider,
            item.url,
        )
    )
    return selections


def _sanitize_cover(
    source_path: Path,
    output_path: Path,
    *,
    max_source_pixels: int,
    max_dimension: int,
    max_cached_bytes: int,
) -> None:
    try:
        with Image.open(source_path) as probe:
            image_format = (probe.format or "").upper()
            width, height = probe.size
            if image_format not in _ALLOWED_IMAGE_FORMATS:
                raise CoverInvalid(f"Unsupported cover image format: {image_format or 'unknown'}")
            if width <= 0 or height <= 0 or width * height > max_source_pixels:
                raise CoverInvalid("Cover image dimensions exceed the configured safety limit")
            probe.verify()

        with Image.open(source_path) as source:
            source.load()
            sanitized = ImageOps.exif_transpose(source)
            bands = sanitized.getbands()
            normalized = sanitized.convert("RGBA" if "A" in bands else "RGB")
            normalized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            normalized.save(output_path, format="PNG", optimize=True)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as exc:
        raise CoverInvalid("Downloaded cover is not a safe supported image") from exc

    if output_path.stat().st_size > max_cached_bytes:
        raise CoverInvalid("Sanitized cover exceeds the configured cache byte limit")


__all__ = [
    "CoverCache",
    "CoverInvalid",
    "CoverMaterialized",
    "CoverNotFound",
    "CoverService",
    "CoverUnavailable",
    "DossierIdentityConflict",
    "DossierNotFound",
]
