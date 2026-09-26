from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.models import (
    Asset,
    Contributor,
    Edition,
    LibraryEntry,
    SourceRecord,
    SourceRecordLink,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PersonalizationSettingsUpdate,
    PreferenceKey,
)
from bukmatika.personalization.service import PersonalizationService


@dataclass(frozen=True, slots=True)
class RecommendationFixture:
    slug: str
    title: str
    author: str
    subject: str
    asset_format: str
    publication_year: int
    owned: bool


FIXTURES = (
    RecommendationFixture(
        slug="best",
        title="Browser Maritime Recommendation",
        author="Ada Navigator",
        subject="Maritime History",
        asset_format="EPUB",
        publication_year=1894,
        owned=False,
    ),
    RecommendationFixture(
        slug="owned",
        title="Browser Owned Maritime Book",
        author="Grace Cartographer",
        subject="Maritime History",
        asset_format="EPUB",
        publication_year=1881,
        owned=True,
    ),
    RecommendationFixture(
        slug="format-only",
        title="Browser Astronomy EPUB",
        author="Orion Reader",
        subject="Astronomy",
        asset_format="EPUB",
        publication_year=1910,
        owned=False,
    ),
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _subject(database_session: AsyncSession, name: str) -> Subject:
    normalized = name.casefold()
    existing = await database_session.scalar(
        select(Subject).where(Subject.normalized_name == normalized)
    )
    if existing is not None:
        return existing
    subject = Subject(display_name=name, normalized_name=normalized)
    database_session.add(subject)
    await database_session.flush()
    return subject


async def _fixture_work(
    database_session: AsyncSession,
    *,
    principal_id: UUID,
    fixture: RecommendationFixture,
) -> Work:
    provider_record_id = f"{principal_id}-{fixture.slug}"
    source_record = await database_session.scalar(
        select(SourceRecord).where(
            SourceRecord.provider == "browser_recommendations",
            SourceRecord.provider_record_id == provider_record_id,
        )
    )
    if source_record is not None:
        work_id = await database_session.scalar(
            select(SourceRecordLink.entity_id).where(
                SourceRecordLink.source_record_id == source_record.id,
                SourceRecordLink.entity_type == "work",
            )
        )
        if work_id is None:
            raise RuntimeError("Browser recommendation source record lost its work link")
        existing = await database_session.get(Work, work_id)
        if existing is None:
            raise RuntimeError("Browser recommendation source record points to a missing work")
        return existing

    work = Work(
        canonical_title=fixture.title,
        normalized_title=fixture.title.casefold(),
    )
    contributor = Contributor(
        display_name=fixture.author,
        normalized_name=fixture.author.casefold(),
    )
    database_session.add_all((work, contributor))
    await database_session.flush()

    database_session.add(
        WorkContributor(
            work_id=work.id,
            contributor_id=contributor.id,
            role="author",
        )
    )
    subject = await _subject(database_session, fixture.subject)
    database_session.add(WorkSubject(work_id=work.id, subject_id=subject.id))

    edition = Edition(
        work_id=work.id,
        title=f"{fixture.title} Browser Edition",
        language="en",
        publication_year=fixture.publication_year,
        publisher="Bukmatika browser proof",
        edition_statement="Deterministic personalized recommendation fixture",
    )
    database_session.add(edition)
    await database_session.flush()
    database_session.add(
        Asset(
            edition_id=edition.id,
            format=fixture.asset_format,
            media_type=(
                "application/epub+zip"
                if fixture.asset_format == "EPUB"
                else "application/pdf"
            ),
            remote_url=(
                f"https://example.invalid/browser-recommendations/"
                f"{provider_record_id}.{fixture.asset_format.casefold()}"
            ),
        )
    )

    source_record = SourceRecord(
        provider="browser_recommendations",
        provider_record_id=provider_record_id,
        canonical_url=f"https://example.invalid/browser-recommendations/{provider_record_id}",
    )
    database_session.add(source_record)
    await database_session.flush()
    database_session.add(
        SourceRecordLink(
            source_record_id=source_record.id,
            entity_type="work",
            entity_id=work.id,
            relationship="describes",
        )
    )
    await database_session.flush()
    return work


async def seed(principal_id: UUID) -> None:
    async with session_scope() as database_session:
        works: dict[str, Work] = {}
        for fixture in FIXTURES:
            work = await _fixture_work(
                database_session,
                principal_id=principal_id,
                fixture=fixture,
            )
            works[fixture.slug] = work
            if fixture.owned:
                existing_entry = await database_session.scalar(
                    select(LibraryEntry).where(
                        LibraryEntry.principal_id == principal_id,
                        LibraryEntry.work_id == work.id,
                    )
                )
                if existing_entry is None:
                    database_session.add(
                        LibraryEntry(
                            principal_id=principal_id,
                            work_id=work.id,
                            status="saved",
                        )
                    )
        await database_session.flush()

        personalization = PersonalizationService(
            session_scope_factory=_scope(database_session)
        )
        await personalization.set_explicit_preference(
            principal_id=principal_id,
            request=ExplicitPreferenceRequest(
                key=PreferenceKey.SUBJECT_INTERESTS,
                value={"subjects": ["Maritime History"]},
            ),
        )
        await personalization.set_explicit_preference(
            principal_id=principal_id,
            request=ExplicitPreferenceRequest(
                key=PreferenceKey.FORMAT_PREFERRED,
                value={"format": "EPUB"},
            ),
        )
        await personalization.update_settings(
            principal_id=principal_id,
            update=PersonalizationSettingsUpdate(
                ai_enabled=False,
                learning_enabled=True,
                autonomy_level=0,
            ),
        )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: browser_recommendations_seed.py <principal-id>")
    asyncio.run(seed(UUID(sys.argv[1])))


if __name__ == "__main__":
    main()
