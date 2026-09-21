import asyncio

import pytest
from pydantic import HttpUrl

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import DiscoveryCandidate, SearchIntent


class RecordingAdapter:
    def __init__(self, name: str, record_count: int) -> None:
        self.name = name
        self.record_count = record_count
        self.last_limit: int | None = None

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        self.last_limit = intent.limit
        return [_record(self.name, index) for index in range(self.record_count)]


class SlowAdapter:
    name = "slow"

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        await asyncio.sleep(0.05)
        return [_record(self.name, 1)]


class FailingAdapter:
    name = "failing"

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        raise RuntimeError("provider unavailable")


def _record(source: str, index: int) -> DiscoveredRecord:
    record_id = f"{source}-{index}"
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=source,
            source_record_id=record_id,
            work_key=f"{source}:{record_id}",
            title=f"Book {index}",
            landing_url=HttpUrl(f"https://example.org/{record_id}"),
        ),
        source_payload={"id": record_id},
        parser_version="test-v1",
    )


def test_provider_registry_rejects_duplicate_authority_names() -> None:
    first = RecordingAdapter("duplicate", 1)
    second = RecordingAdapter("duplicate", 1)
    with pytest.raises(ValueError, match="duplicate discovery provider"):
        ProviderRegistry(
            [
                ProviderRegistration(first, max_results=5, timeout_seconds=1),
                ProviderRegistration(second, max_results=5, timeout_seconds=1),
            ]
        )


async def test_discovery_caps_provider_and_total_records() -> None:
    adapter = RecordingAdapter("bounded", 10)
    service = DiscoveryService(
        ProviderRegistry(
            [ProviderRegistration(adapter, max_results=4, timeout_seconds=1)]
        ),
        session_timeout_seconds=1,
        max_records=3,
    )
    batch = await service.discover(
        SearchIntent(query="history", limit=9),
        service.create_session(),
    )

    assert adapter.last_limit == 4
    assert len(batch.records) == 3
    assert len(batch.response.candidates) == 3
    assert batch.response.source_status["bounded"].result_count == 4


async def test_provider_timeout_and_failure_do_not_collapse_search() -> None:
    fast = RecordingAdapter("fast", 1)
    service = DiscoveryService(
        ProviderRegistry(
            [
                ProviderRegistration(fast, max_results=5, timeout_seconds=1),
                ProviderRegistration(SlowAdapter(), max_results=5, timeout_seconds=0.001),
                ProviderRegistration(FailingAdapter(), max_results=5, timeout_seconds=1),
            ]
        ),
        session_timeout_seconds=1,
        max_records=10,
    )
    batch = await service.discover(
        SearchIntent(query="history", limit=10),
        service.create_session(),
    )

    assert [candidate.source for candidate in batch.response.candidates] == ["fast"]
    assert batch.response.source_status["slow"].status == "timeout"
    assert batch.response.source_status["failing"].status == "error"
    assert "slow" in batch.response.source_errors
    assert "failing" in batch.response.source_errors
