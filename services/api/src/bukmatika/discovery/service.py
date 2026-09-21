import asyncio
from collections.abc import Iterable
from dataclasses import dataclass

from bukmatika.discovery.base import DiscoveredRecord, DiscoveryAdapter, DiscoveryIngestor
from bukmatika.domain import DiscoveryCandidate, DiscoveryResponse, RightsState, SearchIntent


@dataclass(slots=True)
class _AdapterResult:
    name: str
    records: list[DiscoveredRecord]
    error: str | None = None


class DiscoveryService:
    def __init__(self, adapters: list[DiscoveryAdapter]) -> None:
        self._adapters = adapters

    async def search(
        self,
        intent: SearchIntent,
        ingestor: DiscoveryIngestor,
    ) -> DiscoveryResponse:
        results = await asyncio.gather(
            *(self._safe_search(adapter, intent) for adapter in self._adapters)
        )
        errors: dict[str, str] = {}
        for result in results:
            if result.error is not None:
                errors[result.name] = result.error

        records = self._deduplicate(record for result in results for record in result.records)
        for record in records:
            await ingestor.ingest(record)

        candidates = [record.candidate for record in records]
        candidates.sort(key=self._rank, reverse=True)
        return DiscoveryResponse(
            intent=intent,
            candidates=candidates[: intent.limit],
            sources_queried=[adapter.name for adapter in self._adapters],
            source_errors=errors,
        )

    @staticmethod
    async def _safe_search(
        adapter: DiscoveryAdapter,
        intent: SearchIntent,
    ) -> _AdapterResult:
        try:
            return _AdapterResult(name=adapter.name, records=await adapter.search(intent))
        except Exception as exc:  # source degradation must not collapse federated search
            return _AdapterResult(
                name=adapter.name,
                records=[],
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
