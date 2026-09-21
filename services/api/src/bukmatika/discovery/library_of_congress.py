import asyncio
import re
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from urllib.parse import urlsplit, urlunsplit

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


class LibraryOfCongressAdapter:
    """Digitized books from the public loc.gov JSON API."""

    name = "library_of_congress"
    endpoint = "https://www.loc.gov/books/"
    parser_version = "library-of-congress-json-v1"
    max_response_bytes = 4_000_000

    _supported_media_types: ClassVar[dict[str, str]] = {
        "application/epub+zip": "EPUB",
        "application/pdf": "PDF",
        "application/xhtml+xml": "HTML",
        "text/html": "HTML",
        "text/plain": "TXT",
    }
    _supported_suffixes: ClassVar[dict[str, str]] = {
        ".epub": "EPUB",
        ".htm": "HTML",
        ".html": "HTML",
        ".pdf": "PDF",
        ".txt": "TXT",
    }

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._hydrate_limit = asyncio.Semaphore(settings.source_concurrency)

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        response = await self._client.get(
            self.endpoint,
            params=self._params(intent),
            headers=self._headers(),
            timeout=self._settings.http_timeout_seconds,
        )
        response.raise_for_status()
        if len(response.content) > self.max_response_bytes:
            raise ValueError("Library of Congress search response exceeded size limit")
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, Mapping) else []
        if not isinstance(results, list):
            return []
        hydrated = await asyncio.gather(
            *(self._hydrate(row) for row in results if isinstance(row, Mapping))
        )
        return [record for record in hydrated if record is not None]

    async def _hydrate(self, row: Mapping[str, Any]) -> DiscoveredRecord | None:
        item_url = self._item_url(row.get("id"))
        if item_url is None:
            return self._record(row, None)
        item_payload: Mapping[str, Any] | None = None
        try:
            async with self._hydrate_limit:
                response = await self._client.get(
                    item_url,
                    params={"fo": "json", "at": "item,resources"},
                    headers=self._headers(),
                    timeout=self._settings.http_timeout_seconds,
                )
                response.raise_for_status()
                if len(response.content) > self.max_response_bytes:
                    raise ValueError("Library of Congress item response exceeded size limit")
                raw = response.json()
                if isinstance(raw, Mapping):
                    item_payload = raw
        except (httpx.HTTPError, ValueError):
            item_payload = None
        return self._record(row, item_payload)

    def _headers(self) -> dict[str, str]:
        user_agent = self._settings.user_agent
        if self._settings.contact_email:
            user_agent = f"{user_agent} ({self._settings.contact_email})"
        return {"Accept": "application/json", "User-Agent": user_agent}

    @classmethod
    def _params(cls, intent: SearchIntent) -> httpx.QueryParams:
        terms = [
            value
            for value in (intent.query, intent.title, intent.author, intent.subject)
            if value
        ]
        filters = ["digitized:true"]
        if intent.language:
            filters.append(f"language:{intent.language}")
        return httpx.QueryParams(
            {
                "q": " ".join(terms),
                "fo": "json",
                "at": "results",
                "c": intent.limit,
                "fa": "|".join(filters),
            }
        )

    @classmethod
    def _record(
        cls,
        row: Mapping[str, Any],
        item_payload: Mapping[str, Any] | None,
    ) -> DiscoveredRecord | None:
        item = cls._item(item_payload)
        source = item if item else row
        raw_id = cls._string(source.get("id")) or cls._string(row.get("id"))
        landing = cls._item_url(raw_id)
        title = cls._string(source.get("title")) or cls._string(row.get("title"))
        if landing is None or title is None:
            return None
        record_id = cls._record_id(landing)
        if record_id is None:
            return None

        contributors = (
            cls._strings(source.get("contributor_names"))
            or cls._strings(source.get("contributor"))
            or cls._strings(row.get("contributor"))
        )
        languages = cls._strings(source.get("language")) or cls._strings(row.get("language"))
        subjects = (
            cls._strings(source.get("subject_headings"))
            or cls._strings(source.get("subjects"))
            or cls._strings(row.get("subject"))
        )
        year = cls._year(source) or cls._year(row)
        resources = cls._resources(item_payload)
        assets = cls._assets(resources)
        restricted = cls._restricted(source, resources)
        rights = cls._rights(source, landing, restricted)
        assets = [asset.model_copy(update={"rights": list(rights)}) for asset in assets]
        identifiers = cls._identifiers(record_id, source)

        candidate = DiscoveryCandidate(
            source=cls.name,
            source_record_id=record_id,
            record_kind="edition",
            work_key=f"library_of_congress:{record_id}",
            edition_keys=[f"library_of_congress:{record_id}"],
            identifiers=identifiers,
            title=title,
            authors=contributors[:12],
            first_publish_year=year,
            languages=languages[:12],
            subjects=subjects[:24],
            landing_url=HttpUrl(landing),
            formats=sorted({asset.format for asset in assets}),
            assets=assets,
            rights=rights,
            source_score=0.87,
        )
        return DiscoveredRecord(
            candidate=candidate,
            source_payload={
                "search": cls._safe_mapping(row),
                "item": cls._safe_mapping(item),
                "resources": [cls._safe_mapping(value) for value in resources],
            },
            parser_version=cls.parser_version,
        )

    @classmethod
    def _rights(
        cls,
        item: Mapping[str, Any],
        landing_url: str,
        restricted: bool,
    ) -> list[RightsEvidence]:
        evidence_url = HttpUrl(landing_url)
        if restricted:
            return [
                RightsEvidence(
                    state=RightsState.RESTRICTED,
                    source=cls.name,
                    basis=(
                        "Library of Congress metadata marks the item or a digital resource as "
                        "access/download restricted."
                    ),
                    evidence_url=evidence_url,
                    confidence=0.98,
                )
            ]
        statements = (
            cls._strings(item.get("rights"))
            + cls._strings(item.get("rights_advisory"))
            + cls._strings(item.get("access_advisory"))
        )
        basis = (
            "Library of Congress rights/access statements are retained as evidence, but resource "
            "availability alone is not promoted to copyright or unattended-download authority."
        )
        if statements:
            basis = f"{basis} Statements: {'; '.join(statements[:4])}"
        return [
            RightsEvidence(
                state=RightsState.UNKNOWN,
                source=cls.name,
                basis=basis,
                evidence_url=evidence_url,
                confidence=0.95,
            )
        ]

    @classmethod
    def _assets(cls, resources: list[Mapping[str, Any]]) -> list[DiscoveredAsset]:
        assets: list[DiscoveredAsset] = []
        seen: set[str] = set()
        for resource in resources:
            for url, media_type, size, source_kind in cls._resource_files(resource):
                if url in seen:
                    continue
                format_name = cls._format(url, media_type)
                if format_name is None:
                    continue
                seen.add(url)
                assets.append(
                    DiscoveredAsset(
                        name=url.rsplit("/", 1)[-1] or format_name.casefold(),
                        url=HttpUrl(url),
                        format=format_name,
                        media_type=media_type,
                        size_bytes=size,
                        source_kind=source_kind,
                    )
                )
        return assets

    @classmethod
    def _resource_files(
        cls,
        resource: Mapping[str, Any],
    ) -> list[tuple[str, str | None, int | None, str]]:
        result: list[tuple[str, str | None, int | None, str]] = []
        for field in ("pdf", "fulltext_file", "fulltext_derivative"):
            value = cls._https_url(resource.get(field))
            if value:
                result.append((value, cls._media_type_from_url(value), None, field))
        files = resource.get("files", [])
        for file_row in cls._flatten_mappings(files):
            value = cls._https_url(
                file_row.get("url") or file_row.get("download") or file_row.get("filename")
            )
            if value is None:
                continue
            media_type = cls._string(file_row.get("mimetype"))
            result.append(
                (
                    value,
                    media_type,
                    cls._size(file_row.get("size")),
                    "resource-file",
                )
            )
        return result

    @classmethod
    def _restricted(
        cls,
        item: Mapping[str, Any],
        resources: list[Mapping[str, Any]],
    ) -> bool:
        if cls._truthy(item.get("access_restricted")):
            return True
        for resource in resources:
            if cls._truthy(resource.get("download_restricted")):
                return True
            for file_row in cls._flatten_mappings(resource.get("files", [])):
                if cls._truthy(file_row.get("rights_restricted")):
                    return True
        return False

    @classmethod
    def _identifiers(
        cls,
        record_id: str,
        item: Mapping[str, Any],
    ) -> dict[str, list[str]]:
        result = {"library_of_congress": [record_id]}
        lccn = cls._string(item.get("library_of_congress_control_number"))
        if lccn:
            result["lccn"] = [lccn]
        return result

    @staticmethod
    def _item(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if payload is None:
            return {}
        item = payload.get("item", {})
        return item if isinstance(item, Mapping) else {}

    @staticmethod
    def _resources(payload: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
        if payload is None:
            return []
        resources = payload.get("resources", [])
        if not isinstance(resources, list):
            return []
        return [item for item in resources if isinstance(item, Mapping)]

    @staticmethod
    def _item_url(value: object) -> str | None:
        raw = value.strip() if isinstance(value, str) else ""
        if not raw:
            return None
        parsed = urlsplit(raw)
        hostname = (parsed.hostname or "").casefold()
        if hostname not in {"loc.gov", "www.loc.gov"}:
            return None
        return urlunsplit(("https", "www.loc.gov", parsed.path, "", ""))

    @staticmethod
    def _record_id(url: str) -> str | None:
        match = re.search(r"/item/(.+?)/?$", url)
        return match.group(1) if match else None

    @classmethod
    def _format(cls, url: str, media_type: str | None) -> str | None:
        if media_type:
            direct = cls._supported_media_types.get(media_type.split(";", 1)[0].casefold())
            if direct:
                return direct
        path = urlsplit(url).path.casefold()
        for suffix, format_name in cls._supported_suffixes.items():
            if path.endswith(suffix):
                return format_name
        return None

    @classmethod
    def _media_type_from_url(cls, url: str) -> str | None:
        format_name = cls._format(url, None)
        mapping = {
            "EPUB": "application/epub+zip",
            "HTML": "text/html",
            "PDF": "application/pdf",
            "TXT": "text/plain",
        }
        return mapping.get(format_name or "")

    @classmethod
    def _year(cls, row: Mapping[str, Any]) -> int | None:
        for field in ("date_issued", "date", "dates_of_publication"):
            values = cls._strings(row.get(field))
            if not values and isinstance(row.get(field), str):
                values = [str(row[field])]
            for value in values:
                match = re.search(r"\b(1[0-9]{3}|2[0-9]{3})\b", value)
                if match:
                    return int(match.group(1))
        return None

    @staticmethod
    def _flatten_mappings(value: object) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        if isinstance(value, Mapping):
            result.append(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for item in value:
                result.extend(LibraryOfCongressAdapter._flatten_mappings(item))
        return result

    @staticmethod
    def _https_url(value: object) -> str | None:
        raw = value.strip() if isinstance(value, str) else ""
        if not raw:
            return None
        if raw.startswith("//"):
            return f"https:{raw}"
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))

    @staticmethod
    def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
        return {str(key): item for key, item in value.items()}

    @staticmethod
    def _truthy(value: object) -> bool:
        return value is True or (
            isinstance(value, str) and value.strip().casefold() in {"true", "1", "yes"}
        )

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
