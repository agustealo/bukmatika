from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError

from bukmatika.discovery.ranking import (
    DiscoveryFormatPreference,
    DiscoveryRankingProfile,
)
from bukmatika.persistence.personalization_models import PreferenceClaim
from bukmatika.personalization.domain import PreferenceInfluence, PreferenceKey, PreferenceScopeType


def build_discovery_ranking_profile(
    claims: Iterable[PreferenceClaim],
) -> DiscoveryRankingProfile:
    selected: tuple[int, int, datetime, UUID, PreferenceClaim, str] | None = None

    for claim in claims:
        if claim.status != "active" or claim.key != PreferenceKey.FORMAT_PREFERRED.value:
            continue
        if claim.source not in ("explicit", "inferred"):
            continue
        if claim.scope_value:
            continue
        if claim.scope_type not in (
            PreferenceScopeType.GLOBAL.value,
            PreferenceScopeType.DISCOVERY.value,
        ):
            continue
        try:
            influence = PreferenceInfluence.model_validate(claim.influence)
        except ValidationError:
            continue
        if not influence.ranking:
            continue
        raw_format = claim.value.get("format")
        if not isinstance(raw_format, str):
            continue
        normalized_format = " ".join(raw_format.split()).upper()
        if not normalized_format:
            continue

        source_priority = 2 if claim.source == "explicit" else 1
        scope_priority = 1 if claim.scope_type == PreferenceScopeType.DISCOVERY.value else 0
        candidate = (
            source_priority,
            scope_priority,
            claim.updated_at,
            claim.id,
            claim,
            normalized_format,
        )
        if selected is None or candidate[:4] > selected[:4]:
            selected = candidate

    if selected is None:
        return DiscoveryRankingProfile()

    claim = selected[4]
    return DiscoveryRankingProfile(
        format_preference=DiscoveryFormatPreference(
            claim_id=claim.id,
            format=selected[5],
            source=claim.source,  # type: ignore[arg-type]
            confidence=max(0.0, min(1.0, claim.confidence)),
            evidence_count=max(0, claim.evidence_count),
        )
    )


__all__ = ["build_discovery_ranking_profile"]
