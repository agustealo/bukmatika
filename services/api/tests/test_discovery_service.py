import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import HttpUrl

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService, _retry_after_seconds
from bukmatika.domain import (
    DiscoveryCandidate,
    RightsEvidence,
    RightsState,
    SearchIntent,
)


class RecordingAdapter:
    def __init__(self, name: str, record_count: int) -> None:
        self.name = name
        self.record_count = record_count
        self.last_limit: int | None = None

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        self.last_limit = intent.limit
        return [_record(self.name, index) for index in range(self.record_count)]


class StaticAdapter:
    def __init__(self, name: str, records: list[DiscoveredRecord]) -> None:
        self.name = name
        self.records = records
        self.calls = 0

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        del intent
        self.calls += 1
        return self.records


class SlowAdapter:
    name = "slow"

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        await asyncio.sleep(0.05)
        return [_record(self.name, 1)]


class FailingAdapter:
    name = "failing"

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        raise RuntimeError("provider unavailable with private query text")


class HttpStatusAdapter:
    def __init__(self, name: str, status_code: int, *, retry_after: str | None = None) -> None:
        self.name = name
        self.status_code = status_code
        self.retry_after = retry_after

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        request = httpx.Request(
            "GET",
            "https://provider.invalid/search",
            params={"q": "secret-query-text"},
        )
        headers = {"Retry-After": self.retry_after} if self.retry_after is not None else None
        response = httpx.Response(self.status_code, headers=headers, request=request)
        raise httpx.HTTPStatusError(
            "upstream status failure for secret-query-text",
            request=request,
            response=response,
        )


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def info(self, event: str, **event_kw: object) -> object:
        self.events.append((event, event_kw))
        return None

    def error(self, event: str, **event_kw: object) -> object:
        self.events.append((event, event_kw))
        return None


def _record(
    source: str,
    index: int,
    *,
    source_score: float = 0.5,
    languages: list[str] | None = None,
    formats: list[str] | None = None,
    first_publish_year: int | None = None,
    rights: list[RightsEvidence] | None = None,
) -> DiscoveredRecord:
    record_id = f"{source}-{index}"
    return DiscoveredRecord(
        candidate=DiscoveryCandidate(
            source=source,
            source_record_id=record_id,
            work_key=f"{source}:{record_id}",
            title=f"Book {index}",
            first_publish_year=first_publish_year,
            languages=languages or [],
            formats=formats or [],
            rights=rights or [],
            landing_url=HttpUrl(f"https://example.org/{record_id}"),
            source_score=source_score,
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


def test_discovery_preferences_normalize_without_becoming_search_constraints() -> None:
    intent = SearchIntent(
        query="history",
        preferences={
            "languages": [" English ", "ENG", "Latin"],
            "formats": [" EPUB ", "epub"],
            "year_from": 1400,
            "year_to": 1600,
            "rights_states": ["authorized_download", "authorized_download"],
            "sources": ["Project Gutenberg", "project-gutenberg"],
        },
    )

    assert intent.language is None
    assert intent.year_from is None
    assert intent.year_to is None
    assert intent.preferences.languages == ["en", "la"]
    assert intent.preferences.formats == ["epub"]
    assert intent.preferences.year_from == 1400
    assert intent.preferences.year_to == 1600
    assert intent.preferences.rights_states == [RightsState.AUTHORIZED_DOWNLOAD]
    assert intent.preferences.sources == ["project_gutenberg"]


def test_discovery_preferences_reject_inverted_era() -> None:
    with pytest.raises(ValueError, match=r"preferences\.year_from"):
        SearchIntent(
            query="history",
            preferences={"year_from": 1800, "year_to": 1500},
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


async def test_preferences_are_bounded_transparent_and_do_not_filter_sources() -> None:
    dominant = StaticAdapter(
        "dominant_source",
        [_record("dominant_source", 1, source_score=0.95)],
    )
    neutral = StaticAdapter(
        "neutral_source",
        [_record("neutral_source", 1, source_score=0.90)],
    )
    preferred_rights = [
        RightsEvidence(
            state=RightsState.AUTHORIZED_DOWNLOAD,
            source="project_gutenberg",
            basis="Official acquisition link.",
        )
    ]
    preferred = StaticAdapter(
        "project_gutenberg",
        [
            _record(
                "project_gutenberg",
                1,
                source_score=0.87,
                languages=["eng"],
                formats=["EPUB"],
                first_publish_year=1492,
                rights=preferred_rights,
            )
        ],
    )
    service = DiscoveryService(
        ProviderRegistry(
            [
                ProviderRegistration(dominant, max_results=5, timeout_seconds=1),
                ProviderRegistration(neutral, max_results=5, timeout_seconds=1),
                ProviderRegistration(preferred, max_results=5, timeout_seconds=1),
            ]
        ),
        session_timeout_seconds=1,
        max_records=10,
    )

    batch = await service.discover(
        SearchIntent(
            query="history",
            preferences={
                "languages": ["English"],
                "formats": ["EPUB"],
                "year_from": 1400,
                "year_to": 1600,
                "rights_states": ["authorized_download"],
                "sources": ["Project Gutenberg"],
            },
        ),
        service.create_session(),
    )

    assert [candidate.source for candidate in batch.response.candidates] == [
        "dominant_source",
        "project_gutenberg",
        "neutral_source",
    ]
    assert dominant.calls == 1
    assert neutral.calls == 1
    assert preferred.calls == 1
    assert batch.response.sources_queried == [
        "dominant_source",
        "neutral_source",
        "project_gutenberg",
    ]

    preferred_key = "project_gutenberg:project_gutenberg-1"
    preferred_ranking = batch.response.ranking[preferred_key]
    assert preferred_ranking.neutral_score == 0.89
    assert preferred_ranking.preference_boost == 0.05
    assert preferred_ranking.total_score == 0.94
    assert preferred_ranking.matched_preferences == [
        "language",
        "format",
        "era",
        "rights",
        "source",
    ]

    dominant_ranking = batch.response.ranking["dominant_source:dominant_source-1"]
    assert dominant_ranking.neutral_score == 0.95
    assert dominant_ranking.preference_boost == 0
    assert dominant_ranking.total_score == 0.95
    assert batch.response.candidates[1].rights == preferred_rights


async def test_provider_timeout_and_failure_do_not_collapse_search_or_leak_errors() -> None:
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
    slow_status = batch.response.source_status["slow"]
    failing_status = batch.response.source_status["failing"]
    assert slow_status.status == "timeout"
    assert slow_status.error_code == "timeout"
    assert failing_status.status == "error"
    assert failing_status.error_code == "provider_error"
    assert batch.response.source_errors["slow"] == "provider timed out"
    assert batch.response.source_errors["failing"] == "provider search failed"
    assert "private query text" not in str(batch.response.source_errors)


async def test_rate_limit_metadata_is_preserved_without_leaking_upstream_request() -> None:
    logger = RecordingLogger()
    service = DiscoveryService(
        ProviderRegistry(
            [
                ProviderRegistration(
                    HttpStatusAdapter("limited", 429, retry_after="120"),
                    max_results=5,
                    timeout_seconds=1,
                )
            ]
        ),
        session_timeout_seconds=1,
        max_records=10,
        logger=logger,
    )

    batch = await service.discover(
        SearchIntent(query="secret-query-text", limit=5),
        service.create_session(),
    )

    status = batch.response.source_status["limited"]
    assert status.status == "rate_limited"
    assert status.error_code == "rate_limited"
    assert status.http_status_code == 429
    assert status.retry_after_seconds == 120
    assert batch.response.source_errors == {
        "limited": "provider rate limited the request"
    }

    assert logger.events == [
        (
            "discovery.provider.completed",
            {
                "source": "limited",
                "status": "rate_limited",
                "elapsed_ms": status.elapsed_ms,
                "result_count": 0,
                "error_code": "rate_limited",
                "http_status_code": 429,
                "retry_after_seconds": 120,
            },
        )
    ]
    serialized = repr(logger.events) + repr(batch.response.source_errors)
    assert "secret-query-text" not in serialized
    assert "provider.invalid" not in serialized


async def test_non_rate_limit_http_failure_has_safe_http_metadata() -> None:
    service = DiscoveryService(
        ProviderRegistry(
            [
                ProviderRegistration(
                    HttpStatusAdapter("unavailable", 503, retry_after="30"),
                    max_results=5,
                    timeout_seconds=1,
                )
            ]
        ),
        session_timeout_seconds=1,
        max_records=10,
    )

    batch = await service.discover(
        SearchIntent(query="secret-query-text", limit=5),
        service.create_session(),
    )

    status = batch.response.source_status["unavailable"]
    assert status.status == "error"
    assert status.error_code == "http_error"
    assert status.http_status_code == 503
    assert status.retry_after_seconds == 30
    assert batch.response.source_errors["unavailable"] == "provider returned HTTP 503"
    assert "secret-query-text" not in str(batch.response.source_errors)


def test_retry_after_supports_http_date_clamps_and_rejects_invalid_values() -> None:
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    retry_at = now + timedelta(seconds=90)

    assert _retry_after_seconds("120", now=now) == 120
    assert _retry_after_seconds("999999", now=now) == 86_400
    assert _retry_after_seconds(retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT"), now=now) == 90
    assert _retry_after_seconds("not-a-date", now=now) is None
