import re
import xml.etree.ElementTree as ET
from typing import ClassVar

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


class ProjectGutenbergAdapter:
    """Project Gutenberg discovery through its machine-to-machine OPDS feed."""

    name = "project_gutenberg"
    endpoint = "https://www.gutenberg.org/ebooks/search.opds/"
    parser_version = "project-gutenberg-opds-v1"
    max_response_bytes = 2_000_000

    _atom = "{http://www.w3.org/2005/Atom}"
    _acquisition_prefix = "http://opds-spec.org/acquisition"
    _media_types: ClassVar[dict[str, tuple[str, str]]] = {
        "application/epub+zip": ("EPUB", "application/epub+zip"),
        "text/plain": ("TXT", "text/plain"),
        "text/html": ("HTML", "text/html"),
        "application/xhtml+xml": ("HTML", "application/xhtml+xml"),
    }

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        response = await self._client.get(
            self.endpoint,
            params={"query": self._query(intent)},
            headers=self._headers(),
            timeout=self._settings.http_timeout_seconds,
        )
        response.raise_for_status()
        if len(response.content) > self.max_response_bytes:
            raise ValueError("Project Gutenberg OPDS response exceeded size limit")
        return self._parse_feed(response.content)

    def _headers(self) -> dict[str, str]:
        user_agent = self._settings.user_agent
        if self._settings.contact_email:
            user_agent = f"{user_agent} ({self._settings.contact_email})"
        return {
            "Accept": "application/atom+xml;profile=opds-catalog, application/xml;q=0.9",
            "User-Agent": user_agent,
        }

    @staticmethod
    def _query(intent: SearchIntent) -> str:
        parts = [
            value
            for value in (intent.query, intent.title, intent.author, intent.subject)
            if value
        ]
        if intent.language:
            parts.append(intent.language)
        return " ".join(parts)

    @classmethod
    def _parse_feed(cls, payload: bytes) -> list[DiscoveredRecord]:
        root = ET.fromstring(payload)
        return [
            record
            for entry in root.findall(f"{cls._atom}entry")
            if (record := cls._record(entry)) is not None
        ]

    @classmethod
    def _record(cls, entry: ET.Element) -> DiscoveredRecord | None:
        title = cls._text(entry.find(f"{cls._atom}title"))
        raw_id = cls._text(entry.find(f"{cls._atom}id"))
        ebook_id = cls._ebook_id(raw_id, entry)
        if title is None or ebook_id is None:
            return None

        authors = [
            name
            for author in entry.findall(f"{cls._atom}author")
            if (name := cls._text(author.find(f"{cls._atom}name"))) is not None
        ]
        categories = cls._categories(entry)
        languages = [
            item["term"]
            for item in categories
            if "language" in item["scheme"].casefold()
        ]
        subjects = [
            item["term"]
            for item in categories
            if "language" not in item["scheme"].casefold()
        ]
        assets, links = cls._assets(entry)
        landing_url = HttpUrl(f"https://www.gutenberg.org/ebooks/{ebook_id}")
        rights = (
            [
                RightsEvidence(
                    state=RightsState.AUTHORIZED_DOWNLOAD,
                    source=cls.name,
                    basis=(
                        "Project Gutenberg's official OPDS entry exposes acquisition links for "
                        "this exact ebook."
                    ),
                    evidence_url=landing_url,
                    confidence=0.98,
                )
            ]
            if assets
            else [
                RightsEvidence(
                    state=RightsState.UNKNOWN,
                    source=cls.name,
                    basis="The OPDS entry does not expose a supported acquisition asset.",
                    evidence_url=landing_url,
                    confidence=0.95,
                )
            ]
        )

        candidate = DiscoveryCandidate(
            source=cls.name,
            source_record_id=ebook_id,
            record_kind="edition",
            work_key=f"project_gutenberg:{ebook_id}",
            edition_keys=[f"project_gutenberg:{ebook_id}"],
            identifiers={"project_gutenberg": [ebook_id]},
            title=title,
            authors=authors[:12],
            languages=languages[:12],
            subjects=subjects[:24],
            landing_url=landing_url,
            formats=sorted({asset.format for asset in assets}),
            assets=assets,
            rights=rights,
            source_score=0.9,
        )
        return DiscoveredRecord(
            candidate=candidate,
            source_payload={
                "id": raw_id,
                "ebook_id": ebook_id,
                "title": title,
                "authors": authors,
                "categories": categories,
                "acquisition_links": links,
            },
            parser_version=cls.parser_version,
        )

    @classmethod
    def _assets(
        cls,
        entry: ET.Element,
    ) -> tuple[list[DiscoveredAsset], list[dict[str, str]]]:
        assets: list[DiscoveredAsset] = []
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for link in entry.findall(f"{cls._atom}link"):
            href = (link.get("href") or "").strip()
            rel = (link.get("rel") or "").strip()
            media_type = (link.get("type") or "").split(";", 1)[0].strip().casefold()
            if not href or not rel.startswith(cls._acquisition_prefix):
                continue
            links.append({"href": href, "rel": rel, "type": media_type})
            format_info = cls._media_types.get(media_type)
            if format_info is None or href in seen:
                continue
            seen.add(href)
            format_name, canonical_media_type = format_info
            assets.append(
                DiscoveredAsset(
                    name=href.rsplit("/", 1)[-1] or f"ebook.{format_name.casefold()}",
                    url=HttpUrl(href),
                    format=format_name,
                    media_type=canonical_media_type,
                    source_kind="opds-acquisition",
                )
            )
        return assets, links

    @classmethod
    def _ebook_id(cls, raw_id: str | None, entry: ET.Element) -> str | None:
        values = [raw_id or ""]
        values.extend((link.get("href") or "") for link in entry.findall(f"{cls._atom}link"))
        for value in values:
            match = re.search(r"/ebooks/(\d+)", value)
            if match:
                return match.group(1)
        return None

    @classmethod
    def _categories(cls, entry: ET.Element) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for category in entry.findall(f"{cls._atom}category"):
            term = (category.get("term") or "").strip()
            if not term:
                continue
            result.append(
                {
                    "term": term,
                    "label": (category.get("label") or "").strip(),
                    "scheme": (category.get("scheme") or "").strip(),
                }
            )
        return result

    @staticmethod
    def _text(element: ET.Element | None) -> str | None:
        if element is None or element.text is None:
            return None
        value = element.text.strip()
        return value or None
