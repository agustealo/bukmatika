from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from PIL import Image
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.catalog import CatalogResolver
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import (
    DiscoveredAsset,
    DiscoveredCover,
    DiscoveryCandidate,
    RightsEvidence,
    RightsState,
)
from bukmatika.library.covers import CoverCache, CoverService
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Work


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _FixtureDownloader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    async def download(self, url: str, temp_path: Path, **_: Any) -> None:
        self.calls.append(url)
        payload = self.payloads.get(url)
        if payload is None:
            raise RuntimeError(f"No fixture payload for {url}")
        temp_path.write_bytes(payload)


def _image_bytes(*, image_format: str = "JPEG", size: tuple[int, int] = (1200, 1800)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, (42, 84, 126)).save(buffer, format=image_format)
    return buffer.getvalue()


def _record(covers: list[DiscoveredCover]) -> DiscoveredRecord:
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source="cover-provider",
            source_score=1.0,
            source_record_id="cover-record",
            record_kind="edition",
            work_key="cover-provider:cover-record",
            identifiers={"isbn": ["9780000077777"]},
            title="Cover Materialization Test",
            authors=["Cover Author"],
            first_publish_year=1905,
            publisher="Cover Press",
            landing_url=HttpUrl("https://example.org/cover-record"),
            formats=["PDF"],
            assets=[
                DiscoveredAsset(
                    name="book.pdf",
                    url=HttpUrl("https://example.org/cover-record/book.pdf"),
                    format="PDF",
                    media_type="application/pdf",
                    size_bytes=100,
                )
            ],
            covers=covers,
            rights=[
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source="cover-provider",
                    basis="Cover fixture does not authorize book acquisition.",
                )
            ],
        ),
        source_payload={"id": "cover-record"},
        parser_version="cover-materialization-test-v1",
    )


async def _seed(session: AsyncSession, covers: list[DiscoveredCover]) -> UUID:
    await CatalogResolver(CatalogRepository(session)).ingest(_record(covers))
    await session.flush()
    work_id = await session.scalar(
        select(Work.id).where(Work.canonical_title == "Cover Materialization Test")
    )
    assert work_id is not None
    return work_id


async def test_cover_materialization_sanitizes_resizes_and_reuses_cache(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    source_url = "https://images.example.org/full.jpg"
    work_id = await _seed(
        session,
        [
            DiscoveredCover(
                url=HttpUrl(source_url),
                kind="cover",
                media_type="image/jpeg",
            )
        ],
    )
    downloader = _FixtureDownloader({source_url: _image_bytes(size=(2400, 3600))})
    service = CoverService(
        downloader=cast(Any, downloader),
        cache=CoverCache(tmp_path),
        max_source_pixels=10_000_000,
        max_dimension=1600,
        max_cached_bytes=12_582_912,
        session_scope_factory=_scope(session),
    )

    first = await service.cover_for_work(work_id=work_id)
    second = await service.cover_for_work(work_id=work_id)

    assert first.path == second.path
    assert first.path.is_file()
    assert first.path.parts[-5:-2] == ("covers", "objects", first.path.parts[-3])
    assert downloader.calls == [source_url]
    with Image.open(first.path) as image:
        assert image.format == "PNG"
        assert max(image.size) == 1600
        assert image.mode == "RGB"


async def test_cover_materialization_falls_back_from_invalid_cover_to_valid_thumbnail(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    cover_url = "https://images.example.org/broken.jpg"
    thumbnail_url = "https://images.example.org/thumb.png"
    work_id = await _seed(
        session,
        [
            DiscoveredCover(url=HttpUrl(cover_url), kind="cover", media_type="image/jpeg"),
            DiscoveredCover(url=HttpUrl(thumbnail_url), kind="thumbnail", media_type="image/png"),
        ],
    )
    downloader = _FixtureDownloader(
        {
            cover_url: b"not-an-image",
            thumbnail_url: _image_bytes(image_format="PNG", size=(400, 600)),
        }
    )
    service = CoverService(
        downloader=cast(Any, downloader),
        cache=CoverCache(tmp_path),
        max_source_pixels=10_000_000,
        max_dimension=1600,
        max_cached_bytes=12_582_912,
        session_scope_factory=_scope(session),
    )

    materialized = await service.cover_for_work(work_id=work_id)

    assert materialized.path.is_file()
    assert downloader.calls == [cover_url, thumbnail_url]
    with Image.open(materialized.path) as image:
        assert image.format == "PNG"
        assert image.size == (400, 600)
