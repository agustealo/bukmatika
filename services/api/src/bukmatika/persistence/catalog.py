import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    Identifier,
    MetadataAssertion,
    RightsEvidenceRecord,
    RightsEvidenceSubject,
    SourceObservation,
    SourceRecord,
    SourceRecordLink,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)


class CatalogIdentityConflict(RuntimeError):
    pass


class CatalogRepository:
    """Canonical persistence operations for catalog and provider evidence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_source_record(
        self,
        *,
        provider: str,
        provider_record_id: str,
        canonical_url: str,
    ) -> SourceRecord:
        statement = (
            insert(SourceRecord)
            .values(
                provider=provider,
                provider_record_id=provider_record_id,
                canonical_url=canonical_url,
            )
            .on_conflict_do_update(
                constraint="uq_source_provider_record",
                set_={"canonical_url": canonical_url, "updated_at": func.now()},
            )
            .returning(SourceRecord)
        )
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one()

    async def record_source_observation(
        self,
        *,
        source_record_id: UUID,
        payload: dict[str, Any],
        parser_version: str,
    ) -> SourceObservation:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        statement = (
            insert(SourceObservation)
            .values(
                source_record_id=source_record_id,
                payload_sha256=digest,
                payload=payload,
                parser_version=parser_version,
                observation_count=1,
            )
            .on_conflict_do_update(
                constraint="uq_source_observation_payload",
                set_={
                    "last_observed_at": func.now(),
                    "observation_count": SourceObservation.observation_count + 1,
                    "parser_version": parser_version,
                },
            )
            .returning(SourceObservation)
        )
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one()

    async def get_work(self, work_id: UUID) -> Work | None:
        return await self._session.get(Work, work_id)

    async def work_for_identifier(self, scheme: str, normalized_value: str) -> Work | None:
        entity_id = await self._session.scalar(
            select(Identifier.entity_id).where(
                Identifier.entity_type == "work",
                Identifier.scheme == scheme,
                Identifier.normalized_value == normalized_value,
            )
        )
        return await self._session.get(Work, entity_id) if entity_id is not None else None

    async def edition_for_identifier(
        self,
        scheme: str,
        normalized_value: str,
    ) -> Edition | None:
        entity_id = await self._session.scalar(
            select(Identifier.entity_id).where(
                Identifier.entity_type == "edition",
                Identifier.scheme == scheme,
                Identifier.normalized_value == normalized_value,
            )
        )
        return await self._session.get(Edition, entity_id) if entity_id is not None else None

    async def work_for_exact_title_author(
        self,
        normalized_title: str,
        normalized_author: str,
    ) -> Work | None:
        result = await self._session.execute(
            select(Work)
            .join(WorkContributor, WorkContributor.work_id == Work.id)
            .join(Contributor, Contributor.id == WorkContributor.contributor_id)
            .where(
                Work.normalized_title == normalized_title,
                Contributor.normalized_name == normalized_author,
            )
            .distinct()
            .limit(2)
        )
        matches = list(result.scalars())
        return matches[0] if len(matches) == 1 else None

    async def create_work(self, *, title: str, normalized_title: str) -> Work:
        work = Work(canonical_title=title, normalized_title=normalized_title)
        self._session.add(work)
        await self._session.flush()
        return work

    async def create_edition(
        self,
        *,
        work_id: UUID,
        title: str,
        language: str | None,
        publication_year: int | None,
        publisher: str | None,
    ) -> Edition:
        edition = Edition(
            work_id=work_id,
            title=title,
            language=language,
            publication_year=publication_year,
            publisher=publisher,
        )
        self._session.add(edition)
        await self._session.flush()
        return edition

    async def upsert_remote_asset(
        self,
        *,
        edition_id: UUID,
        remote_url: str,
        format_name: str,
        media_type: str | None,
        byte_size: int | None,
    ) -> Asset:
        statement = (
            insert(Asset)
            .values(
                edition_id=edition_id,
                remote_url=remote_url,
                format=format_name,
                media_type=media_type,
                byte_size=byte_size,
            )
            .on_conflict_do_update(
                constraint="uq_assets_edition_remote_url",
                set_={
                    "format": format_name,
                    "media_type": media_type,
                    "byte_size": byte_size,
                    "updated_at": func.now(),
                },
            )
            .returning(Asset)
        )
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one()

    async def add_identifier(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        scheme: str,
        value: str,
        normalized_value: str,
    ) -> None:
        existing = await self._session.scalar(
            select(Identifier).where(
                Identifier.entity_type == entity_type,
                Identifier.scheme == scheme,
                Identifier.normalized_value == normalized_value,
            )
        )
        if existing is not None:
            if existing.entity_id != entity_id:
                raise CatalogIdentityConflict(
                    f"{entity_type} identifier {scheme}:{value} already belongs to another entity"
                )
            return
        self._session.add(
            Identifier(
                entity_type=entity_type,
                entity_id=entity_id,
                scheme=scheme,
                value=value,
                normalized_value=normalized_value,
            )
        )
        await self._session.flush()

    async def add_author(
        self,
        *,
        work_id: UUID,
        display_name: str,
        normalized_name: str,
    ) -> None:
        existing_link = await self._session.scalar(
            select(WorkContributor.contributor_id)
            .join(Contributor, Contributor.id == WorkContributor.contributor_id)
            .where(
                WorkContributor.work_id == work_id,
                WorkContributor.role == "author",
                Contributor.normalized_name == normalized_name,
            )
        )
        if existing_link is not None:
            return
        contributor = Contributor(
            display_name=display_name,
            normalized_name=normalized_name,
        )
        self._session.add(contributor)
        await self._session.flush()
        self._session.add(
            WorkContributor(
                work_id=work_id,
                contributor_id=contributor.id,
                role="author",
            )
        )
        await self._session.flush()

    async def add_subject(
        self,
        *,
        work_id: UUID,
        display_name: str,
        normalized_name: str,
    ) -> None:
        statement = (
            insert(Subject)
            .values(display_name=display_name, normalized_name=normalized_name)
            .on_conflict_do_update(
                constraint="uq_subject_normalized_name",
                set_={"display_name": Subject.display_name},
            )
            .returning(Subject.id)
        )
        subject_id = (await self._session.execute(statement)).scalar_one()
        link = (
            insert(WorkSubject)
            .values(work_id=work_id, subject_id=subject_id)
            .on_conflict_do_nothing()
        )
        await self._session.execute(link)

    async def link_source_record(
        self,
        *,
        source_record_id: UUID,
        entity_type: str,
        entity_id: UUID,
    ) -> None:
        statement = (
            insert(SourceRecordLink)
            .values(
                source_record_id=source_record_id,
                entity_type=entity_type,
                entity_id=entity_id,
                relationship="describes",
            )
            .on_conflict_do_nothing(constraint="uq_source_record_target")
        )
        await self._session.execute(statement)

    async def record_assertion(
        self,
        *,
        entity_type: str,
        entity_id: UUID,
        field_name: str,
        value: Any,
        source_observation_id: UUID,
        confidence: float,
        normalization_method: str | None = None,
    ) -> None:
        existing = await self._session.scalar(
            select(MetadataAssertion.id).where(
                MetadataAssertion.entity_type == entity_type,
                MetadataAssertion.entity_id == entity_id,
                MetadataAssertion.field_name == field_name,
                MetadataAssertion.source_observation_id == source_observation_id,
            )
        )
        if existing is not None:
            return
        self._session.add(
            MetadataAssertion(
                entity_type=entity_type,
                entity_id=entity_id,
                field_name=field_name,
                value=value,
                source_observation_id=source_observation_id,
                confidence=confidence,
                normalization_method=normalization_method,
            )
        )
        await self._session.flush()

    async def record_rights_evidence(
        self,
        *,
        source_observation_id: UUID,
        state: str,
        source: str,
        basis: str,
        evidence_url: str | None,
        license_uri: str | None,
        confidence: float,
    ) -> RightsEvidenceRecord:
        digest_payload = {
            "source_observation_id": str(source_observation_id),
            "state": state,
            "source": source,
            "basis": basis,
            "evidence_url": evidence_url,
            "license_uri": license_uri,
        }
        encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        statement = (
            insert(RightsEvidenceRecord)
            .values(
                source_observation_id=source_observation_id,
                evidence_sha256=digest,
                state=state,
                source=source,
                basis=basis,
                evidence_url=evidence_url,
                license_uri=license_uri,
                confidence=confidence,
            )
            .on_conflict_do_update(
                constraint="uq_rights_evidence_digest",
                set_={"confidence": confidence},
            )
            .returning(RightsEvidenceRecord)
        )
        result = await self._session.execute(statement.execution_options(populate_existing=True))
        return result.scalar_one()

    async def link_rights_evidence(
        self,
        *,
        rights_evidence_id: UUID,
        subject_type: str,
        subject_id: UUID,
    ) -> None:
        statement = (
            insert(RightsEvidenceSubject)
            .values(
                rights_evidence_id=rights_evidence_id,
                subject_type=subject_type,
                subject_id=subject_id,
            )
            .on_conflict_do_nothing()
        )
        await self._session.execute(statement)
