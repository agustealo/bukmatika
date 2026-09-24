from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.discovery.base import DiscoveredRecord
from bukmatika.discovery.ranking import DiscoveryFormatPreference, DiscoveryRankingProfile
from bukmatika.discovery.registry import ProviderRegistration, ProviderRegistry
from bukmatika.discovery.service import DiscoveryService
from bukmatika.domain import DiscoveryCandidate, RightsEvidence, RightsState, SearchIntent
from bukmatika.persistence.models import Principal
from bukmatika.personalization.discovery import discovery_ranking_profile_for_principal
from bukmatika.personalization.domain import (
    ExplicitPreferenceRequest,
    PersonalizationSettingsUpdate,
    PreferenceKey,
)
from bukmatika.personalization.service import PersonalizationService


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _StaticAdapter:
    name = "personalization-proof"

    def __init__(self, candidates: list[DiscoveryCandidate]) -> None:
        self._candidates = candidates

    async def search(self, intent: SearchIntent) -> list[DiscoveredRecord]:
        return [
            DiscoveredRecord(
                candidate=candidate,
                source_payload={"query": intent.query, "id": candidate.source_record_id},
                parser_version="personalization-proof-v1",
            )
            for candidate in self._candidates
        ]


def _candidate(
    record_id: str,
    *,
    format: str,
    rights_state: RightsState = RightsState.UNKNOWN,
) -> DiscoveryCandidate:
    rights = []
    if rights_state is not RightsState.UNKNOWN:
        rights = [
            RightsEvidence(
                state=rights_state,
                source="test",
                basis="adversarial ranking proof",
            )
        ]
    return DiscoveryCandidate(
        source=_StaticAdapter.name,
        source_record_id=record_id,
        work_key=f"personalization:{record_id}",
        title=f"Book {record_id}",
        landing_url=HttpUrl(f"https://example.org/{record_id}"),
        formats=[format],
        rights=rights,
        source_score=0.5,
    )


def _service(candidates: list[DiscoveryCandidate]) -> DiscoveryService:
    return DiscoveryService(
        ProviderRegistry(
            [
                ProviderRegistration(
                    _StaticAdapter(candidates),
                    max_results=10,
                    timeout_seconds=1,
                )
            ]
        ),
        session_timeout_seconds=2,
        max_records=10,
    )


async def test_explicit_format_preference_is_principal_scoped_and_survives_ai_off(
    session: AsyncSession,
) -> None:
    owner = Principal(kind="local", external_subject="personalized-discovery-owner")
    other = Principal(kind="local", external_subject="personalized-discovery-other")
    session.add_all([owner, other])
    await session.flush()
    scope = _scope(session)
    personalization = PersonalizationService(session_scope_factory=scope)
    await personalization.set_explicit_preference(
        principal_id=owner.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": " epub "},
        ),
    )
    await personalization.update_settings(
        principal_id=owner.id,
        update=PersonalizationSettingsUpdate(
            ai_enabled=False,
            learning_enabled=False,
            autonomy_level=0,
        ),
    )

    owner_profile = await discovery_ranking_profile_for_principal(
        principal_id=owner.id,
        session_scope_factory=scope,
    )
    other_profile = await discovery_ranking_profile_for_principal(
        principal_id=other.id,
        session_scope_factory=scope,
    )

    assert owner_profile.format_preference is not None
    assert owner_profile.format_preference.format == "EPUB"
    assert owner_profile.format_preference.source == "explicit"
    assert other_profile.format_preference is None


async def test_explicit_format_preference_reorders_equal_neutral_results_with_explanation() -> None:
    pdf = _candidate("pdf", format="PDF")
    epub = _candidate("epub", format="EPUB")
    service = _service([pdf, epub])
    preference_id = uuid4()
    profile = DiscoveryRankingProfile(
        format_preference=DiscoveryFormatPreference(
            claim_id=preference_id,
            format="EPUB",
            source="explicit",
            confidence=1.0,
            evidence_count=0,
        )
    )

    batch = await service.discover(
        SearchIntent(query="history", limit=10),
        service.create_session(),
        ranking_profile=profile,
    )

    assert [candidate.source_record_id for candidate in batch.response.candidates] == [
        "epub",
        "pdf",
    ]
    assert len(batch.response.personalization) == 1
    explanation = batch.response.personalization[0]
    assert explanation.source_record_id == "epub"
    assert explanation.final_score == explanation.neutral_score + 0.04
    assert explanation.signals[0].preference_claim_id == preference_id
    assert explanation.signals[0].source == "explicit"
    assert explanation.signals[0].value == "EPUB"


async def test_inferred_format_preference_is_confidence_bounded_and_explainable() -> None:
    service = _service([_candidate("epub", format="epub")])
    profile = DiscoveryRankingProfile(
        format_preference=DiscoveryFormatPreference(
            claim_id=uuid4(),
            format="EPUB",
            source="inferred",
            confidence=0.75,
            evidence_count=6,
        )
    )

    batch = await service.discover(
        SearchIntent(query="history", limit=10),
        service.create_session(),
        ranking_profile=profile,
    )

    signal = batch.response.personalization[0].signals[0]
    assert signal.score_delta == 0.015
    assert signal.confidence == 0.75
    assert signal.evidence_count == 6
    assert "75% confidence" in signal.reason


async def test_format_preference_cannot_erase_equal_candidate_public_domain_advantage() -> None:
    public_pdf = _candidate(
        "public-pdf",
        format="PDF",
        rights_state=RightsState.PUBLIC_DOMAIN,
    )
    restricted_epub = _candidate(
        "restricted-epub",
        format="EPUB",
        rights_state=RightsState.RESTRICTED,
    )
    service = _service([restricted_epub, public_pdf])
    profile = DiscoveryRankingProfile(
        format_preference=DiscoveryFormatPreference(
            claim_id=uuid4(),
            format="EPUB",
            source="explicit",
            confidence=1.0,
            evidence_count=0,
        )
    )

    batch = await service.discover(
        SearchIntent(query="history", limit=10),
        service.create_session(),
        ranking_profile=profile,
    )

    assert [candidate.source_record_id for candidate in batch.response.candidates] == [
        "public-pdf",
        "restricted-epub",
    ]
    personalized = batch.response.personalization[0]
    assert personalized.source_record_id == "restricted-epub"
    assert personalized.final_score < 0.58


async def test_discovery_without_profile_remains_neutral_and_emits_no_personalization() -> None:
    service = _service([
        _candidate("b", format="EPUB"),
        _candidate("a", format="PDF"),
    ])

    batch = await service.discover(
        SearchIntent(query="history", limit=10),
        service.create_session(),
    )

    assert [candidate.source_record_id for candidate in batch.response.candidates] == ["a", "b"]
    assert batch.response.personalization == []
