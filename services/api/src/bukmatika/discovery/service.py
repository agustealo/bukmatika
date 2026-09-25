import asyncio
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, cast
from uuid import UUID, uuid4

import httpx
import structlog

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.domain import (
    DiscoveryCandidate,
    DiscoveryPreferenceDimension,
    DiscoveryPreferences,
    DiscoveryRankingExplanation,
    DiscoveryResponse,
    DiscoverySourceStatus,
    RightsState,
    SearchIntent,
    canonical_format,
    canonical_language,
    canonical_source,
)
from bukmatika.observability import StructuredEventLogger

_MAX_RETRY_AFTER_SECONDS = 86_400
_PREFERENCE_DIMENSION_BOOST = 0.01
_MAX_PREFERENCE_BOOST = 0.05
ProviderStatus = Literal["ok", "error", "timeout", "rate_limited"]
ProviderErrorCode = Literal[
    "timeout",
    "rate_limited",
    "http_error",
    "transport_error",
    "provider_error",
]


@dataclass(frozen=True, slots=True)
class SearchSession:
    id: UUID
    timeout_seconds: float
    max_records: int
    started_at: float


@dataclass(frozen=True, slots=True)
class DiscoveryBatch:
    records: list[DiscoveredRecord]
    response: DiscoveryResponse


@dataclass(slots=True)
class _AdapterResult:
    name: str
    records: list[DiscoveredRecord]
    status: ProviderStatus
    elapsed_ms: int
    error: str | None = None
    error_code: ProviderErrorCode | None = None
    http_status_code: int | None = None
    retry_after_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class _RankedRecord:
    record: DiscoveredRecord
    neutral_score: float
    preference_boost: float
    matched_preferences: tuple[DiscoveryPreferenceDimension, ...]

    @property
    def total_score(self) -> float:
        return self.neutral_score + self.preference_boost

    @property
    def key(self) -> str:
        candidate = self.record.candidate
        return f"{candidate.source}:{candidate.source_record_id}"

    def explanation(self) -> DiscoveryRankingExplanation:
        return DiscoveryRankingExplanation(
            neutral_score=round(self.neutral_score, 4),
            preference_boost=round(self.preference_boost, 4),
            total_score=round(self.total_score, 4),
            matched_preferences=list(self.matched_preferences),
        )


class DiscoveryService:
    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        session_timeout_seconds: float,
        max_records: int,
        logger: StructuredEventLogger | None = None,
    ) -> None:
        if session_timeout_seconds <= 0:
            raise ValueError("session_timeout_seconds must be positive")
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._registry = registry
        self._session_timeout_seconds = session_timeout_seconds
        self._max_records = max_records
        self._logger = logger or cast(
            StructuredEventLogger,
            structlog.get_logger("bukmatika.discovery"),
        )

    def create_session(self) -> SearchSession:
        return SearchSession(
            id=uuid4(),
            timeout_seconds=self._session_timeout_seconds,
            max_records=self._max_records,
            started_at=time.monotonic(),
        )

    async def discover(
        self,
        intent: SearchIntent,
        session: SearchSession,
    ) -> DiscoveryBatch:
        registrations = self._registry.active()
        results = await asyncio.gather(
            *(self._safe_search(registration, intent, session) for registration in registrations)
        )

        for result in results:
            self._emit_provider_telemetry(result)

        errors = {
            result.name: result.error
            for result in results
            if result.error is not None
        }
        records = self._deduplicate(
            record for result in results for record in result.records
        )
        ranked_records = [
            self._rank_record(record, intent.preferences)
            for record in records
        ]
        ranked_records.sort(
            key=lambda item: (
                -item.total_score,
                -item.neutral_score,
                item.record.candidate.source,
                item.record.candidate.source_record_id,
            )
        )
        bounded_ranked_records = ranked_records[: session.max_records]
        displayed_ranked_records = bounded_ranked_records[: intent.limit]
        bounded_records = [item.record for item in bounded_ranked_records]
        elapsed_ms = max(0, int((time.monotonic() - session.started_at) * 1000))
        source_status = {
            result.name: DiscoverySourceStatus(
                status=result.status,
                elapsed_ms=result.elapsed_ms,
                result_count=len(result.records),
                error_code=result.error_code,
                http_status_code=result.http_status_code,
                retry_after_seconds=result.retry_after_seconds,
            )
            for result in results
        }

        return DiscoveryBatch(
            records=bounded_records,
            response=DiscoveryResponse(
                session_id=session.id,
                elapsed_ms=elapsed_ms,
                intent=intent,
                candidates=[
                    item.record.candidate for item in displayed_ranked_records
                ],
                ranking={
                    item.key: item.explanation() for item in displayed_ranked_records
                },
                sources_queried=[registration.name for registration in registrations],
                source_errors=errors,
                source_status=source_status,
            ),
        )

    @staticmethod
    async def _safe_search(
        registration: ProviderRegistration,
        intent: SearchIntent,
        session: SearchSession,
    ) -> _AdapterResult:
        started_at = time.monotonic()
        elapsed = started_at - session.started_at
        remaining = session.timeout_seconds - elapsed
        if remaining <= 0:
            return _AdapterResult(
                name=registration.name,
                records=[],
                status="timeout",
                elapsed_ms=0,
                error="provider timed out",
                error_code="timeout",
            )

        timeout_seconds = min(registration.timeout_seconds, remaining)
        scoped_intent = intent.model_copy(
            update={"limit": min(intent.limit, registration.max_results)}
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                records = await registration.adapter.search(scoped_intent)
            return _AdapterResult(
                name=registration.name,
                records=records[: registration.max_results],
                status="ok",
                elapsed_ms=_elapsed_ms(started_at),
            )
        except TimeoutError:
            return _AdapterResult(
                name=registration.name,
                records=[],
                status="timeout",
                elapsed_ms=_elapsed_ms(started_at),
                error="provider timed out",
                error_code="timeout",
            )
        except Exception as exc:  # source degradation must not collapse federated search
            return _classify_provider_failure(
                registration.name,
                exc,
                elapsed_ms=_elapsed_ms(started_at),
            )

    def _emit_provider_telemetry(self, result: _AdapterResult) -> None:
        self._logger.info(
            "discovery.provider.completed",
            source=result.name,
            status=result.status,
            elapsed_ms=result.elapsed_ms,
            result_count=len(result.records),
            error_code=result.error_code,
            http_status_code=result.http_status_code,
            retry_after_seconds=result.retry_after_seconds,
        )

    @staticmethod
    def _deduplicate(records: Iterable[DiscoveredRecord]) -> list[DiscoveredRecord]:
        unique: dict[tuple[str, str], DiscoveredRecord] = {}
        for record in records:
            candidate = record.candidate
            key = (candidate.source, candidate.source_record_id)
            unique.setdefault(key, record)
        return list(unique.values())

    @classmethod
    def _rank_record(
        cls,
        record: DiscoveredRecord,
        preferences: DiscoveryPreferences,
    ) -> _RankedRecord:
        candidate = record.candidate
        neutral_score = cls._rank(candidate)
        matched_preferences = cls._matched_preferences(candidate, preferences)
        preference_boost = min(
            _MAX_PREFERENCE_BOOST,
            len(matched_preferences) * _PREFERENCE_DIMENSION_BOOST,
        )
        return _RankedRecord(
            record=record,
            neutral_score=neutral_score,
            preference_boost=preference_boost,
            matched_preferences=matched_preferences,
        )

    @staticmethod
    def _matched_preferences(
        candidate: DiscoveryCandidate,
        preferences: DiscoveryPreferences,
    ) -> tuple[DiscoveryPreferenceDimension, ...]:
        matched: list[DiscoveryPreferenceDimension] = []

        preferred_languages = set(preferences.languages)
        candidate_languages = {
            canonical_language(value) for value in candidate.languages if value.strip()
        }
        if preferred_languages and preferred_languages.intersection(candidate_languages):
            matched.append("language")

        preferred_formats = set(preferences.formats)
        candidate_formats = {
            canonical_format(value)
            for value in (
                *candidate.formats,
                *(asset.format for asset in candidate.assets),
            )
            if value.strip()
        }
        if preferred_formats and preferred_formats.intersection(candidate_formats):
            matched.append("format")

        if (
            (preferences.year_from is not None or preferences.year_to is not None)
            and candidate.first_publish_year is not None
        ):
            lower = preferences.year_from or 1
            upper = preferences.year_to or 3000
            if lower <= candidate.first_publish_year <= upper:
                matched.append("era")

        preferred_rights = set(preferences.rights_states)
        candidate_rights = {evidence.state for evidence in candidate.rights}
        if preferred_rights and preferred_rights.intersection(candidate_rights):
            matched.append("rights")

        preferred_sources = set(preferences.sources)
        if preferred_sources and canonical_source(candidate.source) in preferred_sources:
            matched.append("source")

        return tuple(matched)

    @staticmethod
    def _rank(candidate: DiscoveryCandidate) -> float:
        rights_bonus = 0.0
        states = {item.state for item in candidate.rights}
        restrictive = {
            RightsState.BORROW_ONLY,
            RightsState.PREVIEW_ONLY,
            RightsState.RESTRICTED,
        }
        if not states.intersection(restrictive) and (
            RightsState.PUBLIC_DOMAIN in states or RightsState.OPEN_LICENSE in states
        ):
            rights_bonus = 0.08
        metadata_bonus = min(
            0.07,
            0.01 * bool(candidate.authors)
            + 0.01 * bool(candidate.first_publish_year)
            + 0.01 * bool(candidate.languages)
            + 0.04 * min(len(candidate.subjects), 4) / 4,
        )
        return candidate.source_score + rights_bonus + metadata_bonus


def _classify_provider_failure(
    source: str,
    error: Exception,
    *,
    elapsed_ms: int,
) -> _AdapterResult:
    if isinstance(error, httpx.TimeoutException):
        return _AdapterResult(
            name=source,
            records=[],
            status="timeout",
            elapsed_ms=elapsed_ms,
            error="provider timed out",
            error_code="timeout",
        )

    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        retry_after_seconds = _retry_after_seconds(error.response.headers.get("Retry-After"))
        if status_code == 429:
            return _AdapterResult(
                name=source,
                records=[],
                status="rate_limited",
                elapsed_ms=elapsed_ms,
                error="provider rate limited the request",
                error_code="rate_limited",
                http_status_code=status_code,
                retry_after_seconds=retry_after_seconds,
            )
        return _AdapterResult(
            name=source,
            records=[],
            status="error",
            elapsed_ms=elapsed_ms,
            error=f"provider returned HTTP {status_code}",
            error_code="http_error",
            http_status_code=status_code,
            retry_after_seconds=retry_after_seconds,
        )

    if isinstance(error, httpx.TransportError):
        return _AdapterResult(
            name=source,
            records=[],
            status="error",
            elapsed_ms=elapsed_ms,
            error="provider request failed",
            error_code="transport_error",
        )

    return _AdapterResult(
        name=source,
        records=[],
        status="error",
        elapsed_ms=elapsed_ms,
        error="provider search failed",
        error_code="provider_error",
    )


def _retry_after_seconds(
    value: str | None,
    *,
    now: datetime | None = None,
) -> int | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None

    if candidate.isdecimal():
        return min(int(candidate), _MAX_RETRY_AFTER_SECONDS)

    try:
        retry_at = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)

    current_time = now or datetime.now(UTC)
    seconds = max(0, math.ceil((retry_at - current_time).total_seconds()))
    return min(seconds, _MAX_RETRY_AFTER_SECONDS)


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.monotonic() - started_at) * 1000))


def normalize_candidates(records: list[DiscoveredRecord]) -> list[DiscoveryCandidate]:
    return [record.candidate for record in records]
