from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.main import app
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    SourceRecord,
    SourceRecordLink,
    Subject,
    Work,
    WorkSubject,
)
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PreferenceInfluence,
    PreferenceKey,
    PreferenceScopeType,
)
from bukmatika.personalization.recommendations import PersonalizedRecommendationService
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"recommendation-{suffix}-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


async def _subject(session: AsyncSession, name: str) -> Subject:
    normalized = name.casefold()
    existing = await session.scalar(select(Subject).where(Subject.normalized_name == normalized))
    if existing is not None:
        return existing
    subject = Subject(display_name=name, normalized_name=normalized)
    session.add(subject)
    await session.flush()
    return subject


async def _work(
    session: AsyncSession,
    *,
    title: str,
    subject_name: str,
    asset_format: str,
    source: str,
    record_id: str,
    language: str = "en",
    publication_year: int = 1900,
) -> Work:
    work = Work(canonical_title=title, normalized_title=title.casefold())
    session.add(work)
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=title,
        language=language,
        publication_year=publication_year,
    )
    session.add(edition)
    await session.flush()
    session.add(
        Asset(
            edition_id=edition.id,
            format=asset_format,
            remote_url=f"https://example.test/{record_id}.{asset_format.casefold()}",
        )
    )

    subject = await _subject(session, subject_name)
    session.add(WorkSubject(work_id=work.id, subject_id=subject.id))

    source_record = SourceRecord(
        provider=source,
        provider_record_id=record_id,
        canonical_url=f"https://example.test/records/{record_id}",
    )
    session.add(source_record)
    await session.flush()
    session.add(
        SourceRecordLink(
            source_record_id=source_record.id,
            entity_type="work",
            entity_id=work.id,
            relationship="describes",
        )
    )
    await session.flush()
    return work


async def test_recommendations_explain_preference_fit_and_exclude_owned_work(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "owner")
    other = await _principal(session, "other")
    best = await _work(
        session,
        title="Atlantic Worlds",
        subject_name="Maritime History",
        asset_format="EPUB",
        source="gutenberg",
        record_id=f"best-{uuid4()}",
    )
    owned = await _work(
        session,
        title="Owned Maritime Book",
        subject_name="Maritime History",
        asset_format="EPUB",
        source="gutenberg",
        record_id=f"owned-{uuid4()}",
    )
    format_only = await _work(
        session,
        title="Astronomy in EPUB",
        subject_name="Astronomy",
        asset_format="EPUB",
        source="openlibrary",
        record_id=f"format-{uuid4()}",
    )
    session.add(LibraryEntry(principal_id=principal.id, work_id=owned.id, status="saved"))
    await session.flush()

    personalization = PersonalizationService(session_scope_factory=_scope(session))
    subject_claim = await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["Maritime History"]},
        ),
    )
    format_claim = await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "EPUB"},
        ),
    )
    await personalization.set_explicit_preference(
        principal_id=other.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["Astronomy"]},
        ),
    )

    service = PersonalizedRecommendationService(session_scope_factory=_scope(session))
    result = await service.recommend(principal_id=principal.id, limit=8)

    assert [item.work_id for item in result.items] == [best.id, format_only.id]
    assert owned.id not in {item.work_id for item in result.items}
    assert result.active_ranking_claims == 2
    assert result.supported_ranking_claims == 2

    first = result.items[0]
    assert first.title == "Atlantic Worlds"
    assert first.fit_score == 1.5
    assert [reason.signal for reason in first.reasons] == ["subject", "format"]
    assert [reason.claim_id for reason in first.reasons] == [subject_claim.claim_id, format_claim.claim_id]
    assert [reason.source for reason in first.reasons] == ["explicit", "explicit"]
    assert first.reasons[0].matched_values == ["Maritime History"]
    assert first.reasons[0].contribution == 1.0
    assert first.reasons[1].matched_values == ["EPUB"]
    assert first.reasons[1].contribution == 0.5

    second = result.items[1]
    assert second.work_id == format_only.id
    assert second.fit_score == 0.5
    assert [reason.signal for reason in second.reasons] == ["format"]


async def test_recommendation_library_exclusion_is_principal_scoped(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "scope-owner")
    other = await _principal(session, "scope-other")
    candidate = await _work(
        session,
        title="Shared Catalog Candidate",
        subject_name="Book History",
        asset_format="PDF",
        source="loc",
        record_id=f"shared-{uuid4()}",
    )
    session.add(LibraryEntry(principal_id=other.id, work_id=candidate.id, status="saved"))
    await session.flush()

    personalization = PersonalizationService(session_scope_factory=_scope(session))
    await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["Book History"]},
        ),
    )

    result = await PersonalizedRecommendationService(
        session_scope_factory=_scope(session)
    ).recommend(principal_id=principal.id, limit=8)

    assert [item.work_id for item in result.items] == [candidate.id]


async def test_non_ranking_and_non_discovery_scoped_claims_do_not_recommend(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "ignored")
    await _work(
        session,
        title="Ignored Match",
        subject_name="Ancient History",
        asset_format="PDF",
        source="gutenberg",
        record_id=f"ignored-{uuid4()}",
    )

    personalization = PersonalizationService(session_scope_factory=_scope(session))
    await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["Ancient History"]},
            scope_type=PreferenceScopeType.READER,
            scope_value="reader",
        ),
    )
    await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "PDF"},
            influence=PreferenceInfluence(
                ranking=False,
                presentation=True,
                automation=False,
            ),
        ),
    )

    result = await PersonalizedRecommendationService(
        session_scope_factory=_scope(session)
    ).recommend(principal_id=principal.id, limit=8)

    assert result.items == []
    assert result.active_ranking_claims == 0
    assert result.supported_ranking_claims == 0


async def test_forgotten_preference_stops_influencing_recommendations(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "forgotten")
    await _work(
        session,
        title="Former Match",
        subject_name="Cartography",
        asset_format="PDF",
        source="loc",
        record_id=f"forgotten-{uuid4()}",
    )
    personalization = PersonalizationService(session_scope_factory=_scope(session))
    claim = await personalization.set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.SUBJECT_INTERESTS,
            value={"subjects": ["Cartography"]},
        ),
    )
    recommendation_service = PersonalizedRecommendationService(
        session_scope_factory=_scope(session)
    )

    before = await recommendation_service.recommend(principal_id=principal.id, limit=8)
    assert len(before.items) == 1

    await personalization.forget_preference(
        principal_id=principal.id,
        claim_id=claim.claim_id,
    )
    after = await recommendation_service.recommend(principal_id=principal.id, limit=8)

    assert after.items == []
    assert after.active_ranking_claims == 0


def test_personalized_recommendations_route_is_mounted() -> None:
    assert "/v1/personalization/recommendations" in set(app.openapi()["paths"])
