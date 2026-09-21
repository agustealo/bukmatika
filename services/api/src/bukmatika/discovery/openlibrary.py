from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from bukmatika.config import Settings
from bukmatika.domain import DiscoveryCandidate, RightsEvidence, RightsState, SearchIntent


class OpenLibraryAdapter:
    name = "open_library"
    endpoint = "https://openlibrary.org/search.json"

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def search(self, intent: SearchIntent) -> list[DiscoveryCandidate]:
        params = self._params(intent)
        response = await self._client.get(
            self.endpoint,
            params=params,
            headers=self._headers(),
            timeout=self._settings.http_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        docs = payload.get("docs", [])
        if not isinstance(docs, list):
            return []
        return [candidate for row in docs if (candidate := self._candidate(row)) is not None]

    def _headers(self) -> dict[str, str]:
        user_agent = self._settings.user_agent
        if self._settings.contact_email:
            user_agent = f"{user_agent} ({self._settings.contact_email})"
        return {"Accept": "application/json", "User-Agent": user_agent}

    @staticmethod
    def _params(intent: SearchIntent) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "limit": intent.limit,
            "fields": (
                "key,title,author_name,first_publish_year,language,subject,edition_key,"
                "ebook_access,public_scan_b,ia"
            ),
        }
        clauses: list[str] = []
        if intent.query:
            clauses.append(intent.query)
        if intent.title:
            params["title"] = intent.title
        if intent.author:
            params["author"] = intent.author
        if intent.subject:
            params["subject"] = intent.subject
        if intent.language:
            clauses.append(f"language:{intent.language}")
        if intent.year_from or intent.year_to:
            low = intent.year_from or 1
            high = intent.year_to or 3000
            clauses.append(f"first_publish_year:[{low} TO {high}]")
        if clauses:
            params["q"] = " ".join(clauses)
        return params

    @classmethod
    def _candidate(cls, row: object) -> DiscoveryCandidate | None:
        if not isinstance(row, Mapping):
            return None
        key = cls._string(row.get("key"))
        title = cls._string(row.get("title"))
        if not key or not title:
            return None

        rights = cls._rights(row)
        return DiscoveryCandidate(
            source=cls.name,
            source_record_id=key,
            work_key=f"openlibrary:{key.removeprefix('/')}",
            edition_keys=[
                f"openlibrary:{value}" for value in cls._strings(row.get("edition_key"))
            ],
            title=title,
            authors=cls._strings(row.get("author_name")),
            first_publish_year=cls._integer(row.get("first_publish_year")),
            languages=cls._strings(row.get("language")),
            subjects=cls._strings(row.get("subject"))[:24],
            landing_url=f"https://openlibrary.org{quote(key, safe='/')}",
            formats=[],
            rights=rights,
            source_score=0.85,
        )

    @staticmethod
    def _rights(row: Mapping[str, Any]) -> list[RightsEvidence]:
        access = OpenLibraryAdapter._string(row.get("ebook_access"))
        public_scan = row.get("public_scan_b") is True
        key = OpenLibraryAdapter._string(row.get("key")) or ""
        evidence_url = f"https://openlibrary.org{quote(key, safe='/')}" if key else None

        if access == "public" or public_scan:
            return [
                RightsEvidence(
                    state=RightsState.PUBLIC_DOMAIN,
                    source="open_library",
                    basis=(
                        "Open Library marks the work/edition as public ebook access or public scan."
                    ),
                    evidence_url=evidence_url,
                    confidence=0.8,
                )
            ]
        if access == "borrowable":
            return [
                RightsEvidence(
                    state=RightsState.BORROW_ONLY,
                    source="open_library",
                    basis="Open Library marks ebook access as borrowable.",
                    evidence_url=evidence_url,
                    confidence=0.95,
                )
            ]
        return [
            RightsEvidence(
                state=RightsState.UNKNOWN,
                source="open_library",
                basis="Open Library search metadata does not establish downloadable rights.",
                evidence_url=evidence_url,
                confidence=0.7,
            )
        ]

    @staticmethod
    def _string(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _strings(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @staticmethod
    def _integer(value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
