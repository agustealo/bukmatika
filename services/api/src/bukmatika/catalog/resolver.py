import re
import unicodedata
from uuid import UUID

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.persistence.catalog import CatalogRepository
from bukmatika.persistence.models import Edition, Work


class CatalogResolver:
    """Resolve provider observations into canonical Work and Edition identities."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def ingest(self, record: DiscoveredRecord) -> None:
        candidate = record.candidate
        source = await self._repository.upsert_source_record(
            provider=candidate.source,
            provider_record_id=candidate.source_record_id,
            canonical_url=str(candidate.landing_url),
        )
        observation = await self._repository.record_source_observation(
            source_record_id=source.id,
            payload=record.source_payload,
            parser_version=record.parser_version,
        )

        edition = await self._edition_by_strong_identifier(record)
        work: Work | None = None
        if edition is not None:
            work = await self._repository.get_work(edition.work_id)
            if work is None:
                raise RuntimeError(
                    f"Edition {edition.id} references missing Work {edition.work_id}"
                )

        if work is None and candidate.record_kind == "work":
            work = await self._repository.work_for_identifier(
                "provider_work_key",
                self._normalize_identifier(candidate.work_key),
            )

        if work is None and candidate.authors:
            work = await self._repository.work_for_exact_title_author(
                self._normalize_text(candidate.title),
                self._normalize_text(candidate.authors[0]),
            )

        if work is None:
            work = await self._repository.create_work(
                title=candidate.title,
                normalized_title=self._normalize_text(candidate.title),
            )
            for author in candidate.authors:
                await self._repository.add_author(
                    work_id=work.id,
                    display_name=author,
                    normalized_name=self._normalize_text(author),
                )

        if candidate.record_kind == "work":
            await self._repository.add_identifier(
                entity_type="work",
                entity_id=work.id,
                scheme="provider_work_key",
                value=candidate.work_key,
                normalized_value=self._normalize_identifier(candidate.work_key),
            )

        for subject in candidate.subjects:
            await self._repository.add_subject(
                work_id=work.id,
                display_name=subject,
                normalized_name=self._normalize_text(subject),
            )

        if candidate.record_kind == "edition" and edition is None:
            edition = await self._repository.create_edition(
                work_id=work.id,
                title=candidate.title,
                language=candidate.languages[0] if candidate.languages else None,
                publication_year=candidate.first_publish_year,
                publisher=candidate.publisher,
            )
            for scheme, values in candidate.identifiers.items():
                for value in values:
                    await self._repository.add_identifier(
                        entity_type="edition",
                        entity_id=edition.id,
                        scheme=scheme,
                        value=value,
                        normalized_value=self._normalize_identifier(value),
                    )

        if edition is not None:
            for asset in candidate.assets:
                await self._repository.upsert_remote_asset(
                    edition_id=edition.id,
                    remote_url=str(asset.url),
                    format_name=asset.format,
                    media_type=asset.media_type,
                    byte_size=asset.size_bytes,
                )

        for evidence in candidate.rights:
            await self._repository.record_rights_evidence(
                source_observation_id=observation.id,
                state=evidence.state.value,
                source=evidence.source,
                basis=evidence.basis,
                evidence_url=str(evidence.evidence_url) if evidence.evidence_url else None,
                license_uri=str(evidence.license_uri) if evidence.license_uri else None,
                confidence=evidence.confidence,
            )

        target_type = "edition" if edition is not None else "work"
        target_id = edition.id if edition is not None else work.id
        await self._repository.link_source_record(
            source_record_id=source.id,
            entity_type=target_type,
            entity_id=target_id,
        )
        await self._record_assertions(
            record=record,
            observation_id=observation.id,
            work_id=work.id,
            edition=edition,
        )

    async def _edition_by_strong_identifier(
        self,
        record: DiscoveredRecord,
    ) -> Edition | None:
        if record.candidate.record_kind != "edition":
            return None
        for scheme, values in record.candidate.identifiers.items():
            for value in values:
                edition = await self._repository.edition_for_identifier(
                    scheme,
                    self._normalize_identifier(value),
                )
                if edition is not None:
                    return edition
        return None

    async def _record_assertions(
        self,
        *,
        record: DiscoveredRecord,
        observation_id: UUID,
        work_id: UUID,
        edition: Edition | None,
    ) -> None:
        candidate = record.candidate
        work_fields = {
            "title": candidate.title,
            "authors": candidate.authors,
            "subjects": candidate.subjects,
            "first_publish_year": candidate.first_publish_year,
        }
        for field_name, value in work_fields.items():
            if value in (None, [], ""):
                continue
            await self._repository.record_assertion(
                entity_type="work",
                entity_id=work_id,
                field_name=field_name,
                value=value,
                source_observation_id=observation_id,
                confidence=candidate.source_score,
                normalization_method="bukmatika-normalize-v1",
            )

        if edition is None:
            return
        edition_fields = {
            "title": candidate.title,
            "publication_year": candidate.first_publish_year,
            "publisher": candidate.publisher,
            "languages": candidate.languages,
            "formats": candidate.formats,
        }
        for field_name, value in edition_fields.items():
            if value in (None, [], ""):
                continue
            await self._repository.record_assertion(
                entity_type="edition",
                entity_id=edition.id,
                field_name=field_name,
                value=value,
                source_observation_id=observation_id,
                confidence=candidate.source_score,
                normalization_method="bukmatika-normalize-v1",
            )

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return " ".join(normalized.split())

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold().strip()
        return re.sub(r"[\s-]+", "", normalized)
