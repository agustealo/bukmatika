from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_learning import _format_claim, _principal, _reader_open_event, _scope
from test_personalized_recommendations import _work

from bukmatika.personalization.domain import ExplicitPreferenceRequest, PreferenceKey
from bukmatika.personalization.learning import (
    LearningService,
    OutcomeCreate,
    OutcomeValue,
    _format_confidence,
)
from bukmatika.personalization.recommendations import PersonalizedRecommendationService
from bukmatika.personalization.service import PersonalizationService


@pytest.mark.parametrize(
    ("support", "margin", "expected"),
    [
        (4, 2, 0.74),
        (4, 4, 0.77),
        (5, 5, 0.81),
        (6, 6, 0.85),
        (7, 7, 0.89),
        (8, 8, 0.90),
        (12, 12, 0.90),
    ],
)
def test_format_confidence_curve_is_fixed_and_bounded(
    support: int,
    margin: int,
    expected: float,
) -> None:
    assert _format_confidence(support=support, margin=margin) == expected


async def test_format_inference_thresholds_and_minimum_qualifying_confidence(
    session: AsyncSession,
) -> None:
    service = LearningService(session_scope_factory=_scope(session))

    weak = await _principal(session, "calibration-weak")
    for index in range(3):
        await _reader_open_event(
            session,
            principal=weak,
            suffix=f"calibration-weak-pdf-{index}",
            format_name="PDF",
        )
    assert await service.refresh_format_preference(principal_id=weak.id) is None
    assert await _format_claim(session, weak.id) is None

    narrow = await _principal(session, "calibration-narrow")
    for index in range(4):
        await _reader_open_event(
            session,
            principal=narrow,
            suffix=f"calibration-narrow-pdf-{index}",
            format_name="PDF",
        )
    for index in range(3):
        await _reader_open_event(
            session,
            principal=narrow,
            suffix=f"calibration-narrow-epub-{index}",
            format_name="EPUB",
        )
    assert await service.refresh_format_preference(principal_id=narrow.id) is None
    assert await _format_claim(session, narrow.id) is None

    qualifying = await _principal(session, "calibration-qualifying")
    for index in range(4):
        await _reader_open_event(
            session,
            principal=qualifying,
            suffix=f"calibration-qualifying-pdf-{index}",
            format_name="PDF",
        )
    for index in range(2):
        await _reader_open_event(
            session,
            principal=qualifying,
            suffix=f"calibration-qualifying-epub-{index}",
            format_name="EPUB",
        )

    claim_id = await service.refresh_format_preference(principal_id=qualifying.id)
    assert claim_id is not None
    claim = await _format_claim(session, qualifying.id)
    assert claim is not None
    assert claim.id == claim_id
    assert claim.value == {"format": "PDF"}
    assert claim.confidence == 0.74
    assert claim.evidence_count == 4


async def test_outcome_feedback_has_deterministic_bounds_and_contradiction_threshold(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "calibration-outcomes")
    service = LearningService(session_scope_factory=_scope(session))
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"calibration-outcomes-pdf-{index}",
            format_name="PDF",
        )
    for index in range(2):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"calibration-outcomes-epub-{index}",
            format_name="EPUB",
        )
    claim_id = await service.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None
    claim = await _format_claim(session, principal.id)
    assert claim is not None
    assert claim.confidence == 0.74

    await service.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.REJECTED,
            entity_type="preference_claim",
            entity_id=claim.id,
        ),
    )
    await session.refresh(claim)
    assert claim.confidence == pytest.approx(0.54)
    assert claim.status == "active"

    await service.record_outcome(
        principal_id=principal.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.REJECTED,
            entity_type="preference_claim",
            entity_id=claim.id,
        ),
    )
    await session.refresh(claim)
    assert claim.confidence == pytest.approx(0.34)
    assert claim.status == "contradicted"

    positive = await _principal(session, "calibration-positive-cap")
    for index in range(8):
        await _reader_open_event(
            session,
            principal=positive,
            suffix=f"calibration-positive-pdf-{index}",
            format_name="PDF",
        )
    positive_claim_id = await service.refresh_format_preference(principal_id=positive.id)
    assert positive_claim_id is not None
    positive_claim = await _format_claim(session, positive.id)
    assert positive_claim is not None
    assert positive_claim.confidence == 0.90

    await service.record_outcome(
        principal_id=positive.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.HELPED,
            entity_type="preference_claim",
            entity_id=positive_claim.id,
        ),
    )
    await session.refresh(positive_claim)
    assert positive_claim.confidence == pytest.approx(0.95)

    await service.record_outcome(
        principal_id=positive.id,
        request=OutcomeCreate(
            outcome=OutcomeValue.ACCEPTED,
            entity_type="preference_claim",
            entity_id=positive_claim.id,
        ),
    )
    await session.refresh(positive_claim)
    assert positive_claim.confidence == pytest.approx(0.95)
    assert positive_claim.status == "active"


async def test_inferred_confidence_decays_at_half_life_and_expires_below_floor(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "calibration-decay")
    service = LearningService(session_scope_factory=_scope(session))
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"calibration-decay-pdf-{index}",
            format_name="PDF",
        )

    baseline = datetime.now(UTC)
    claim_id = await service.refresh_format_preference(
        principal_id=principal.id,
        now=baseline,
    )
    assert claim_id is not None
    claim = await _format_claim(session, principal.id)
    assert claim is not None
    assert claim.confidence == 0.77

    changed = await service.apply_decay(
        principal_id=principal.id,
        now=baseline + timedelta(days=90),
    )
    assert changed == 1
    await session.refresh(claim)
    assert claim.confidence == pytest.approx(0.385)
    assert claim.status == "active"

    changed = await service.apply_decay(
        principal_id=principal.id,
        now=baseline + timedelta(days=180),
    )
    assert changed == 1
    await session.refresh(claim)
    assert claim.confidence == pytest.approx(0.1925)
    assert claim.status == "expired"


async def test_calibrated_inference_flows_into_recommendation_and_explicit_authority_wins(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "calibration-recommendation")
    candidate = await _work(
        session,
        title="Calibration PDF Candidate",
        subject_name="Navigation",
        asset_format="PDF",
        source="gutenberg",
        record_id="calibration-pdf-candidate",
    )
    learning = LearningService(session_scope_factory=_scope(session))
    for index in range(4):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"calibration-recommendation-pdf-{index}",
            format_name="PDF",
        )
    for index in range(2):
        await _reader_open_event(
            session,
            principal=principal,
            suffix=f"calibration-recommendation-epub-{index}",
            format_name="EPUB",
        )
    claim_id = await learning.refresh_format_preference(principal_id=principal.id)
    assert claim_id is not None

    recommendation_service = PersonalizedRecommendationService(
        session_scope_factory=_scope(session)
    )
    inferred = await recommendation_service.recommend(principal_id=principal.id, limit=8)
    inferred_item = next(item for item in inferred.items if item.work_id == candidate.id)
    assert inferred_item.fit_score == 0.2775
    assert len(inferred_item.reasons) == 1
    assert inferred_item.reasons[0].signal == "format"
    assert inferred_item.reasons[0].source == "inferred"
    assert inferred_item.reasons[0].confidence == 0.74
    assert inferred_item.reasons[0].contribution == 0.2775

    await PersonalizationService(session_scope_factory=_scope(session)).set_explicit_preference(
        principal_id=principal.id,
        request=ExplicitPreferenceRequest(
            key=PreferenceKey.FORMAT_PREFERRED,
            value={"format": "PDF"},
        ),
    )

    explicit = await recommendation_service.recommend(principal_id=principal.id, limit=8)
    explicit_item = next(item for item in explicit.items if item.work_id == candidate.id)
    assert explicit_item.fit_score == 0.5
    assert len(explicit_item.reasons) == 1
    assert explicit_item.reasons[0].signal == "format"
    assert explicit_item.reasons[0].source == "explicit"
    assert explicit_item.reasons[0].confidence == 1.0
    assert explicit_item.reasons[0].contribution == 0.5
