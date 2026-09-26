from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.domain import canonical_format, canonical_language, canonical_source
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
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import PreferenceClaim
from bukmatika.personalization.domain import PreferenceKey
from bukmatika.personalization.recommendation_domain import (
    PersonalizedRecommendation,
    PersonalizedRecommendationsResponse,
    RecommendationReason,
    RecommendationSignal,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_SUPPORTED_SCOPES = {"global", "discovery"}
_SIGNAL_WEIGHTS: dict[RecommendationSignal, float] = {
    "subject": 1.0,
    "author": 1.0,
    "region": 0.8,
    "period": 0.7,
    "language": 0.6,
    "format": 0.5,
    "source": 0.4,
}
_SUPPORTED_KEYS: dict[str, RecommendationSignal] = {
    PreferenceKey.SUBJECT_INTERESTS.value: "subject",
    PreferenceKey.AUTHOR_INTERESTS.value: "author",
    PreferenceKey.REGION_INTERESTS.value: "region",
    PreferenceKey.PERIOD_INTERESTS.value: "period",
    PreferenceKey.LANGUAGE_PREFERRED.value: "language",
    PreferenceKey.FORMAT_PREFERRED.value: "format",
    PreferenceKey.SOURCE_INSTITUTIONS.value: "source",
}


@dataclass(frozen=True, slots=True)
class _CandidateMetadata:
    title: str
    authors: tuple[str, ...]
    subjects: tuple[str, ...]
    languages: tuple[str, ...]
    formats: tuple[str, ...]
    publication_years: tuple[int, ...]
    source: str
    source_record_id: str


class PersonalizedRecommendationService:
    """Deterministic recommendations over canonical catalog and preference authorities."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def recommend(
        self,
        *,
        principal_id: UUID,
        limit: int,
    ) -> PersonalizedRecommendationsResponse:
        async with self._session_scope() as database_session:
            preference_repository = PersonalizationRepository(database_session)
            claims = await preference_repository.active_claims(principal_id)
            ranking_claims = [
                claim
                for claim in claims
                if claim.scope_type in _SUPPORTED_SCOPES
                and bool(cast(dict[str, Any], claim.influence).get("ranking", False))
            ]
            supported = [claim for claim in ranking_claims if claim.key in _SUPPORTED_KEYS]
            if not supported:
                return PersonalizedRecommendationsResponse(
                    items=[],
                    active_ranking_claims=len(ranking_claims),
                    supported_ranking_claims=0,
                    explanation=(
                        "No active global or discovery ranking preferences currently map to "
                        "canonical recommendation signals."
                    ),
                )

            owned_work_ids = set(
                (
                    await database_session.scalars(
                        select(LibraryEntry.work_id).where(
                            LibraryEntry.principal_id == principal_id
                        )
                    )
                ).all()
            )
            reasons_by_work: defaultdict[UUID, list[RecommendationReason]] = defaultdict(list)
            for claim in supported:
                signal = _SUPPORTED_KEYS[claim.key]
                matches = await _matches_for_claim(database_session, claim=claim, signal=signal)
                contribution = _claim_contribution(claim=claim, signal=signal)
                if contribution <= 0:
                    continue
                for work_id, matched_values in matches.items():
                    if work_id in owned_work_ids or not matched_values:
                        continue
                    reasons_by_work[work_id].append(
                        RecommendationReason(
                            claim_id=claim.id,
                            preference_key=claim.key,
                            source=cast(str, claim.source),
                            confidence=claim.confidence,
                            signal=signal,
                            matched_values=sorted(matched_values, key=str.casefold),
                            contribution=contribution,
                        )
                    )

            if not reasons_by_work:
                return PersonalizedRecommendationsResponse(
                    items=[],
                    active_ranking_claims=len(ranking_claims),
                    supported_ranking_claims=len(supported),
                    explanation=(
                        "Your active ranking preferences are understood, but no persisted catalog "
                        "work outside your library currently matches them."
                    ),
                )

            metadata = await _candidate_metadata(
                database_session,
                work_ids=set(reasons_by_work),
            )
            ranked: list[PersonalizedRecommendation] = []
            for work_id, reasons in reasons_by_work.items():
                candidate = metadata.get(work_id)
                if candidate is None:
                    continue
                fit_score = round(sum(reason.contribution for reason in reasons), 4)
                ranked.append(
                    PersonalizedRecommendation(
                        work_id=work_id,
                        title=candidate.title,
                        authors=list(candidate.authors),
                        subjects=list(candidate.subjects),
                        languages=list(candidate.languages),
                        formats=list(candidate.formats),
                        publication_years=list(candidate.publication_years),
                        source=candidate.source,
                        source_record_id=candidate.source_record_id,
                        fit_score=fit_score,
                        reasons=sorted(
                            reasons,
                            key=lambda reason: (
                                -reason.contribution,
                                reason.preference_key,
                                str(reason.claim_id),
                            ),
                        ),
                    )
                )

            ranked.sort(
                key=lambda item: (
                    -item.fit_score,
                    -len(item.reasons),
                    item.title.casefold(),
                    str(item.work_id),
                )
            )
            return PersonalizedRecommendationsResponse(
                items=ranked[:limit],
                active_ranking_claims=len(ranking_claims),
                supported_ranking_claims=len(supported),
                explanation=(
                    "Recommendations use only active ranking-enabled preferences and canonical "
                    "catalog metadata. Fit scores are preference-match explanations, not rights, "
                    "quality, or acquisition scores."
                ),
            )


async def _matches_for_claim(
    session: AsyncSession,
    *,
    claim: PreferenceClaim,
    signal: RecommendationSignal,
) -> dict[UUID, set[str]]:
    if signal in {"subject", "region"}:
        targets = _normalized_interest_values(claim.value, "subject", "subjects", "region", "regions")
        if not targets:
            return {}
        rows = (
            await session.execute(
                select(WorkSubject.work_id, Subject.display_name)
                .join(Subject, Subject.id == WorkSubject.subject_id)
                .where(Subject.normalized_name.in_(targets))
            )
        ).all()
        return _group_rows(rows)

    if signal == "author":
        targets = _normalized_interest_values(claim.value, "author", "authors")
        if not targets:
            return {}
        rows = (
            await session.execute(
                select(WorkContributor.work_id, Contributor.display_name)
                .join(Contributor, Contributor.id == WorkContributor.contributor_id)
                .where(
                    WorkContributor.role == "author",
                    Contributor.normalized_name.in_(targets),
                )
            )
        ).all()
        return _group_rows(rows)

    if signal == "format":
        targets = {
            canonical_format(value)
            for value in _string_values(claim.value, "format", "formats")
        }
        targets.discard("")
        if not targets:
            return {}
        rows = (
            await session.execute(
                select(Edition.work_id, Asset.format)
                .join(Asset, Asset.edition_id == Edition.id)
                .where(func.lower(Asset.format).in_(targets))
            )
        ).all()
        return _group_rows(rows)

    if signal == "language":
        targets = {
            canonical_language(value)
            for value in _string_values(claim.value, "language", "languages")
        }
        targets.discard("")
        if not targets:
            return {}
        rows = (
            await session.execute(
                select(Edition.work_id, Edition.language).where(Edition.language.is_not(None))
            )
        ).all()
        grouped: defaultdict[UUID, set[str]] = defaultdict(set)
        for work_id, language in rows:
            if language is not None and canonical_language(language) in targets:
                grouped[work_id].add(language)
        return dict(grouped)

    if signal == "period":
        period = _period_bounds(claim.value)
        if period is None:
            return {}
        year_from, year_to = period
        rows = (
            await session.execute(
                select(Edition.work_id, Edition.publication_year).where(
                    Edition.publication_year.is_not(None),
                    Edition.publication_year >= year_from,
                    Edition.publication_year <= year_to,
                )
            )
        ).all()
        return _group_rows(
            [(work_id, str(year)) for work_id, year in rows if year is not None]
        )

    if signal == "source":
        targets = {
            canonical_source(value)
            for value in _string_values(
                claim.value,
                "source",
                "sources",
                "institution",
                "institutions",
            )
        }
        targets.discard("")
        if not targets:
            return {}
        work_rows = (
            await session.execute(
                select(SourceRecordLink.entity_id, SourceRecord.provider)
                .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
                .where(
                    SourceRecordLink.entity_type == "work",
                    SourceRecord.provider.in_(targets),
                )
            )
        ).all()
        edition_rows = (
            await session.execute(
                select(Edition.work_id, SourceRecord.provider)
                .join(
                    SourceRecordLink,
                    (SourceRecordLink.entity_type == "edition")
                    & (SourceRecordLink.entity_id == Edition.id),
                )
                .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
                .where(SourceRecord.provider.in_(targets))
            )
        ).all()
        return _group_rows([*work_rows, *edition_rows])

    return {}


def _claim_contribution(*, claim: PreferenceClaim, signal: RecommendationSignal) -> float:
    weight = _SIGNAL_WEIGHTS[signal]
    authority = 1.0 if claim.source == "explicit" else 0.75 * claim.confidence
    return round(weight * authority, 4)


def _string_values(value: dict[str, Any], *keys: str) -> list[str]:
    found: list[str] = []
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str):
            normalized = " ".join(candidate.split())
            if normalized:
                found.append(normalized)
        elif isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, str):
                    normalized = " ".join(item.split())
                    if normalized:
                        found.append(normalized)
    return list(dict.fromkeys(found))


def _normalized_interest_values(value: dict[str, Any], *keys: str) -> set[str]:
    return {item.casefold() for item in _string_values(value, *keys)}


def _period_bounds(value: dict[str, Any]) -> tuple[int, int] | None:
    lower = value.get("year_from", value.get("start_year"))
    upper = value.get("year_to", value.get("end_year"))
    if isinstance(lower, bool) or isinstance(upper, bool):
        return None
    if not isinstance(lower, int) and not isinstance(upper, int):
        return None
    year_from = lower if isinstance(lower, int) else 1
    year_to = upper if isinstance(upper, int) else 3000
    if not (1 <= year_from <= year_to <= 3000):
        return None
    return year_from, year_to


def _group_rows(rows: list[tuple[UUID, Any]]) -> dict[UUID, set[str]]:
    grouped: defaultdict[UUID, set[str]] = defaultdict(set)
    for work_id, value in rows:
        if value is not None:
            grouped[work_id].add(str(value))
    return dict(grouped)


async def _candidate_metadata(
    session: AsyncSession,
    *,
    work_ids: set[UUID],
) -> dict[UUID, _CandidateMetadata]:
    if not work_ids:
        return {}

    titles = dict(
        (
            await session.execute(
                select(Work.id, Work.canonical_title).where(Work.id.in_(work_ids))
            )
        ).all()
    )
    authors: defaultdict[UUID, set[str]] = defaultdict(set)
    for work_id, name in (
        await session.execute(
            select(WorkContributor.work_id, Contributor.display_name)
            .join(Contributor, Contributor.id == WorkContributor.contributor_id)
            .where(
                WorkContributor.work_id.in_(work_ids),
                WorkContributor.role == "author",
            )
        )
    ).all():
        authors[work_id].add(name)

    subjects: defaultdict[UUID, set[str]] = defaultdict(set)
    for work_id, name in (
        await session.execute(
            select(WorkSubject.work_id, Subject.display_name)
            .join(Subject, Subject.id == WorkSubject.subject_id)
            .where(WorkSubject.work_id.in_(work_ids))
        )
    ).all():
        subjects[work_id].add(name)

    languages: defaultdict[UUID, set[str]] = defaultdict(set)
    years: defaultdict[UUID, set[int]] = defaultdict(set)
    for work_id, language, publication_year in (
        await session.execute(
            select(Edition.work_id, Edition.language, Edition.publication_year).where(
                Edition.work_id.in_(work_ids)
            )
        )
    ).all():
        if language:
            languages[work_id].add(language)
        if publication_year is not None:
            years[work_id].add(publication_year)

    formats: defaultdict[UUID, set[str]] = defaultdict(set)
    for work_id, asset_format in (
        await session.execute(
            select(Edition.work_id, Asset.format)
            .join(Asset, Asset.edition_id == Edition.id)
            .where(Edition.work_id.in_(work_ids))
        )
    ).all():
        formats[work_id].add(asset_format)

    source_options: defaultdict[UUID, set[tuple[str, str]]] = defaultdict(set)
    for work_id, provider, record_id in (
        await session.execute(
            select(
                SourceRecordLink.entity_id,
                SourceRecord.provider,
                SourceRecord.provider_record_id,
            )
            .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
            .where(
                SourceRecordLink.entity_type == "work",
                SourceRecordLink.entity_id.in_(work_ids),
            )
        )
    ).all():
        source_options[work_id].add((provider, record_id))
    for work_id, provider, record_id in (
        await session.execute(
            select(Edition.work_id, SourceRecord.provider, SourceRecord.provider_record_id)
            .join(
                SourceRecordLink,
                (SourceRecordLink.entity_type == "edition")
                & (SourceRecordLink.entity_id == Edition.id),
            )
            .join(SourceRecord, SourceRecord.id == SourceRecordLink.source_record_id)
            .where(Edition.work_id.in_(work_ids))
        )
    ).all():
        source_options[work_id].add((provider, record_id))

    metadata: dict[UUID, _CandidateMetadata] = {}
    for work_id, title in titles.items():
        sources = sorted(source_options.get(work_id, set()))
        if not sources:
            continue
        provider, record_id = sources[0]
        metadata[work_id] = _CandidateMetadata(
            title=title,
            authors=tuple(sorted(authors[work_id], key=str.casefold)),
            subjects=tuple(sorted(subjects[work_id], key=str.casefold)),
            languages=tuple(sorted(languages[work_id], key=str.casefold)),
            formats=tuple(sorted(formats[work_id], key=str.casefold)),
            publication_years=tuple(sorted(years[work_id])),
            source=provider,
            source_record_id=record_id,
        )
    return metadata


__all__ = ["PersonalizedRecommendationService"]
