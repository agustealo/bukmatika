from dataclasses import dataclass
from typing import Any, Protocol

from bukmatika.domain import DiscoveryCandidate, SearchIntent


@dataclass(frozen=True, slots=True)
class DiscoveredRecord:
    candidate: DiscoveryCandidate
    source_payload: dict[str, Any]
    parser_version: str


class DiscoveryAdapter(Protocol):
    name: str

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]: ...


class DiscoveryIngestor(Protocol):
    async def ingest(self, record: DiscoveredRecord) -> None: ...
