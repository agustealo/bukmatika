import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.ranking import DiscoveryRankingProfile, personalization_signals
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.domain import (
    DiscoveryCandidate,
    DiscoveryPersonalizationExplanation,
    DiscoveryPersonalizationSignal,
    DiscoveryResponse,
    DiscoverySourceStatus,
    RightsState,
    SearchIntent,
)


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
    status: Literal["ok", "error", "timeout"]
    elapsed_ms: int
    error: str | None = None


@dataclass(frozen=True, slots=True)
class _ScoredRecord:
    record: DiscoveredRecord
    neutral_score: float
    final_score: float
    signals: tuple[DiscoveryPersonalizationSignal, ...]


class DiscoveryService:
    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        session_timeout_seconds: float,
        max_records: int,
    ) -> None:
        if session_timeout_seconds <= 0:
            raise ValueError("session_timeout_seconds must be positive")
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._registry = registry
        self._session_timeout_seconds = session_timeout_seconds
        self._max_records = max_records

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
        *,
        ranking_profile: DiscoveryRankingProfile | None = None,
    ) -> DiscoveryBatch:
        registrations = self._registry.active()
        results = await asyncio.gather(
            *(self._safe_search(registration, intent, session) for registration in registrations)
        )

        errors = {
            result.name: result.error
            for result in results
            if result.error is not None
        }
        records = self._deduplicate(
            record for result in results for record in result.records
        )
        scored_records = [
            self._score(record, ranking_profile=ranking_profile)
            for record in records
        ]
        scored_records.sort(
            key=lambda item: (
                -item.final_score,
                -item.neutral_score,
                item.record.candidate.source,
                item.record.candidate.source_record_id,
            )
        )
        bounded = scored_records[: session.max_records]
        visible = bounded[: intent.limit]
        bounded_records = [item.record for item in bounded]
        elapsed_ms = max(0, int((time.monotonic() - session.started_at) * 1000))
        source_status = {
            result.name: DiscoverySourceStatus(
                status=result.status,
                elapsed_ms=result.elapsed_ms,
                result_count=len(result.records),
            )
            for result in results
        }

        return DiscoveryBatch(
            records=bounded_records,
            response=DiscoveryResponse(
                session_id=session.id,
                elapsed_ms=elapsed_ms,
                intent=intent,
                candidates=[item.record.candidate for item in visible],
                sources_queried=[registration.name for registration in registrations],
                source_errors=errors,
                source_status=source_status,
                personalization=[
                    DiscoveryPersonalizationExplanation(
                        source=item.record.candidate.source,
                        source_record_id=item.record.candidate.source_record_id,
                        neutral_score=item.neutral_score,
                        final_score=item.final_score,
                        signals=list(item.signals),
                    )
                    for item in visible
                    if item.signals
                ],
            ),
        )

    @classmethod
    def _score(
        cls,
        record: DiscoveredRecord,
        *,
        ranking_profile: DiscoveryRankingProfile | None,
    ) -> _ScoredRecord:
        neutral_score = cls._rank(record.candidate)
        signals = personalization_signals(record.candidate, ranking_profile)
        final_score = neutral_score + sum(signal.score_delta for signal in signals)
        return _ScoredRecord(
            record=record,
            neutral_score=neutral_score,
            final_score=final_score,
            signals=signals,
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
                error="search session deadline reached before provider execution",
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
                elapsed_ms=max(0, int((time.monotonic() - started_at) * 1000)),
            )
        except TimeoutError:
            return _AdapterResult(
                name=registration.name,
                records=[],
                status="timeout",
                elapsed_ms=max(0, int((time.monotonic() - started_at) * 1000)),
                error=f"provider timed out after {timeout_seconds:.2f}s",
            )
        except Exception as exc:  # source degradation must not collapse federated search
            return _AdapterResult(
                name=registration.name,
                records=[],
                status="error",
                elapsed_ms=max(0, int((time.monotonic() - started_at) * 1000)),
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _deduplicate(records: Iterable[DiscoveredRecord]) -> list[DiscoveredRecord]:
        unique: dict[tuple[str, str], DiscoveredRecord] = {}
        for record in records:
            candidate = record.candidate
            key = (candidate.source, candidate.source_record_id)
            unique.setdefault(key, record)
        return list(unique.values())

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
