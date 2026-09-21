import asyncio
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import HttpUrl

from bukmatika.config import Settings
from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.domain import (
    DiscoveredAsset,
    DiscoveryCandidate,
    RightsEvidence,
    RightsState,
    SearchIntent,
)


class InternetArchiveAdapter:
    name = "internet_archive"
    search_endpoint = "https://archive.org/advancedsearch.php"
    metadata_endpoint = "https://archive.org/metadata"
    parser_version = "internet-archive-v1"
    _supported_extensions = {
        ".doc": "DOC",
        ".docx": "DOCX",
        ".epub": "EPUB",
        ".htm": "HTML",
        ".html": "HTML",
        ".pdf": "PDF",
        ".txt": "TXT",
    }
    _media_types = {
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".epub": "application/epub+zip",
        ".htm": "text/html",
        ".html": "text/html",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._hydrate_limit = asyncio.Semaphore(settings.source_concurrency)

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        response = await self._client.get(
            self.search_endpoint,
            params=self._params(intent),
            headers=self._headers(),
            timeout=self._settings.http_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        response_body = payload.get("response", {})
        if not isinstance(response_body, Mapping):
            return []
        docs = response_body.get("docs", [])
        if not isinstance(docs, list):
            return []

        results = await asyncio.gather(
            *(self._hydrate(row) for row in docs if isinstance(row, Mapping))
        )
        return [record for record in results if record is not None]

    async def _hydrate(self, search_row: Mapping[str, Any]) -> DiscoveredRecord | None:
        identifier = self._string(search_row.get("identifier"))
        if identifier is None:
            return None

        item_payload: Mapping[str, Any] | None = None
        try:
            async with self._hydrate_limit:
                response = await self._client.get(
                    f"{self.metadata_endpoint}/{quote(identifier, safe='')}",
                    headers=self._headers(),
                    timeout=self._settings.http_timeout_seconds,
                )
                response.raise_for_status()
                raw_payload = response.json()
                if isinstance(raw_payload, Mapping):
                    item_payload = raw_payload
        except (httpx.HTTPError, ValueError):
            item_payload = None

        return self._record(search_row, item_payload)

    @classmethod
    def _record(
        cls,
        search_row: Mapping[str, Any],
        item_payload: Mapping[str, Any] | None,
    ) -> DiscoveredRecord | None:
        identifier = cls._string(search_row.get("identifier"))
        if identifier is None:
            return None

        metadata = cls._metadata(item_payload)
        title = cls._string(metadata.get("title")) or cls._string(search_row.get("title"))
        if title is None:
            return None

        landing_url = HttpUrl(f"https://archive.org/details/{quote(identifier, safe='')}")
        raw_files = cls._files(item_payload)
        assets = cls._assets(identifier, raw_files)
        creators = cls._strings(metadata.get("creator")) or cls._strings(search_row.get("creator"))
        languages = cls._strings(metadata.get("language")) or cls._strings(
            search_row.get("language")
        )
        subjects = cls._strings(metadata.get("subject")) or cls._strings(search_row.get("subject"))
        year = cls._year(metadata) or cls._year(search_row)
        publisher = cls._string(metadata.get("publisher"))
        rights = cls._rights(metadata, item_payload, landing_url)
        identifiers = cls._identifiers(identifier, metadata)

        source_payload: dict[str, Any] = {
            "search": {str(key): value for key, value in search_row.items()},
        }
        if item_payload is not None:
            source_payload["item"] = {
                "metadata": {str(key): value for key, value in metadata.items()},
                "files": [dict(value) for value in raw_files],
            }

        candidate = DiscoveryCandidate(
            source=cls.name,
            source_record_id=identifier,
            record_kind="edition",
            work_key=f"internet_archive:{identifier}",
            edition_keys=[f"internet_archive:{identifier}"],
            identifiers=identifiers,
            title=title,
            authors=creators[:12],
            first_publish_year=year,
            publisher=publisher,
            languages=languages[:12],
            subjects=subjects[:24],
            landing_url=landing_url,
            formats=sorted({asset.format for asset in assets}),
            assets=assets,
            rights=rights,
            source_score=0.82,
        )
        return DiscoveredRecord(
            candidate=candidate,
            source_payload=source_payload,
            parser_version=cls.parser_version,
        )

    def _headers(self) -> dict[str, str]:
        user_agent = self._settings.user_agent
        if self._settings.contact_email:
            user_agent = f"{user_agent} ({self._settings.contact_email})"
        return {"Accept": "application/json", "User-Agent": user_agent}

    @classmethod
    def _params(cls, intent: SearchIntent) -> list[tuple[str, str | int]]:
        clauses = ["mediatype:texts"]
        if intent.query:
            clauses.append(cls._quoted(intent.query))
        if intent.title:
            clauses.append(f"title:{cls._quoted(intent.title)}")
        if intent.author:
            clauses.append(f"creator:{cls._quoted(intent.author)}")
        if intent.subject:
            clauses.append(f"subject:{cls._quoted(intent.subject)}")
        if intent.language:
            clauses.append(f"language:{cls._quoted(intent.language)}")
        if intent.year_from or intent.year_to:
            low = intent.year_from or 1
            high = intent.year_to or 3000
            clauses.append(f"year:[{low} TO {high}]")

        fields = (
            "identifier",
            "title",
            "creator",
            "date",
            "year",
            "language",
            "subject",
            "rights",
            "licenseurl",
            "access-restricted-item",
        )
        params: list[tuple[str, str | int]] = [
            ("q", " AND ".join(clauses)),
            ("output", "json"),
            ("rows", intent.limit),
            ("page", 1),
        ]
        params.extend(("fl[]", field) for field in fields)
        return params

    @staticmethod
    def _quoted(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    @staticmethod
    def _metadata(item_payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if item_payload is None:
            return {}
        metadata = item_payload.get("metadata", {})
        return metadata if isinstance(metadata, Mapping) else {}

    @staticmethod
    def _files(item_payload: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
        if item_payload is None:
            return []
        files = item_payload.get("files", [])
        if not isinstance(files, list):
            return []
        return [value for value in files if isinstance(value, Mapping)]

    @classmethod
    def _assets(
        cls,
        identifier: str,
        files: list[Mapping[str, Any]],
    ) -> list[DiscoveredAsset]:
        assets: list[DiscoveredAsset] = []
        for row in files:
            name = cls._string(row.get("name"))
            if name is None:
                continue
            extension = cls._extension(name)
            format_name = cls._supported_extensions.get(extension)
            if format_name is None:
                continue
            checksums = {
                key: value
                for key in ("md5", "sha1")
                if (value := cls._string(row.get(key))) is not None
            }
            assets.append(
                DiscoveredAsset(
                    name=name,
                    url=HttpUrl(
                        "https://archive.org/download/"
                        f"{quote(identifier, safe='')}/{quote(name, safe='/')}"
                    ),
                    format=format_name,
                    media_type=cls._media_types.get(extension),
                    size_bytes=cls._size(row.get("size")),
                    source_kind=cls._string(row.get("source")),
                    checksums=checksums,
                )
            )
        return assets

    @classmethod
    def _rights(
        cls,
        metadata: Mapping[str, Any],
        item_payload: Mapping[str, Any] | None,
        evidence_url: HttpUrl,
    ) -> list[RightsEvidence]:
        if cls._restricted(metadata, item_payload):
            return [
                RightsEvidence(
                    state=RightsState.RESTRICTED,
                    source=cls.name,
                    basis=(
                        "Internet Archive metadata marks the item as access restricted; file "
                        "presence is not treated as download authorization."
                    ),
                    evidence_url=evidence_url,
                    confidence=0.98,
                )
            ]

        license_uri = cls._string(metadata.get("licenseurl"))
        if license_uri:
            lowered = license_uri.lower()
            if "creativecommons.org/publicdomain/mark/" in lowered:
                return [
                    RightsEvidence(
                        state=RightsState.PUBLIC_DOMAIN,
                        source=cls.name,
                        basis="The exact item carries a Creative Commons Public Domain Mark URI.",
                        evidence_url=evidence_url,
                        license_uri=HttpUrl(license_uri),
                        confidence=0.9,
                    )
                ]
            if (
                "creativecommons.org/licenses/" in lowered
                or "creativecommons.org/publicdomain/zero/" in lowered
            ):
                return [
                    RightsEvidence(
                        state=RightsState.OPEN_LICENSE,
                        source=cls.name,
                        basis="The exact item carries a recognized Creative Commons license URI.",
                        evidence_url=evidence_url,
                        license_uri=HttpUrl(license_uri),
                        confidence=0.9,
                    )
                ]

        rights = cls._string(metadata.get("rights"))
        if rights:
            basis = (
                "Internet Archive exposes an item-level rights statement, retained as evidence; "
                "the statement alone is not promoted to unattended acquisition authority."
            )
        else:
            basis = (
                "Internet Archive item metadata does not establish a recognized exact-item "
                "license or public-domain authority."
            )
        return [
            RightsEvidence(
                state=RightsState.UNKNOWN,
                source=cls.name,
                basis=basis,
                evidence_url=evidence_url,
                confidence=0.9,
            )
        ]

    @classmethod
    def _identifiers(
        cls,
        identifier: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, list[str]]:
        result = {"internet_archive": [identifier]}
        mapping = {
            "isbn": "isbn",
            "oclc": "oclc-id",
            "lccn": "lccn",
        }
        for scheme, field in mapping.items():
            values = cls._strings(metadata.get(field))
            if values:
                result[scheme] = values
        return result

    @classmethod
    def _year(cls, row: Mapping[str, Any]) -> int | None:
        value = row.get("year")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, list):
            for item in value:
                parsed = cls._year_value(item)
                if parsed is not None:
                    return parsed
        parsed = cls._year_value(value)
        if parsed is not None:
            return parsed
        return cls._year_value(row.get("date"))

    @staticmethod
    def _year_value(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return None
        match = re.search(r"\b(1[0-9]{3}|2[0-9]{3})\b", value)
        return int(match.group(1)) if match else None

    @staticmethod
    def _restricted(
        metadata: Mapping[str, Any],
        item_payload: Mapping[str, Any] | None,
    ) -> bool:
        values = [metadata.get("access-restricted-item")]
        if item_payload is not None:
            values.append(item_payload.get("access-restricted-item"))
        return any(
            value is True
            or (isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"})
            for value in values
        )

    @staticmethod
    def _extension(name: str) -> str:
        lowered = name.lower()
        for extension in InternetArchiveAdapter._supported_extensions:
            if lowered.endswith(extension):
                return extension
        return ""

    @staticmethod
    def _string(value: object) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @staticmethod
    def _size(value: object) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool):
            return value if value >= 0 else None
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return None
