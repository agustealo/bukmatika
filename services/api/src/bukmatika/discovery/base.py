from __future__ import annotations

from typing import Protocol

from bukmatika.domain import DiscoveryCandidate, SearchIntent


class DiscoveryAdapter(Protocol):
    name: str

    async def search(self, intent: SearchIntent) -> list[DiscoveryCandidate]: ...
