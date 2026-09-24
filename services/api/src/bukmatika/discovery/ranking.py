from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from bukmatika.domain import DiscoveryCandidate, DiscoveryPersonalizationSignal

EXPLICIT_FORMAT_PREFERENCE_BOOST = 0.04
INFERRED_FORMAT_PREFERENCE_MAX_BOOST = 0.02


@dataclass(frozen=True, slots=True)
class DiscoveryFormatPreference:
    claim_id: UUID
    format: str
    source: Literal["explicit", "inferred"]
    confidence: float
    evidence_count: int


@dataclass(frozen=True, slots=True)
class DiscoveryRankingProfile:
    format_preference: DiscoveryFormatPreference | None = None


def personalization_signals(
    candidate: DiscoveryCandidate,
    profile: DiscoveryRankingProfile | None,
) -> tuple[DiscoveryPersonalizationSignal, ...]:
    if profile is None or profile.format_preference is None:
        return ()

    preference = profile.format_preference
    available_formats = _candidate_formats(candidate)
    if preference.format not in available_formats:
        return ()

    if preference.source == "explicit":
        score_delta = EXPLICIT_FORMAT_PREFERENCE_BOOST
        reason = f"Ranked higher because {preference.format} is your explicit preferred format."
    else:
        score_delta = round(
            INFERRED_FORMAT_PREFERENCE_MAX_BOOST * max(0.0, min(1.0, preference.confidence)),
            6,
        )
        if score_delta <= 0:
            return ()
        reason = (
            f"Ranked higher because Bukmatika inferred a {preference.format} format preference "
            f"from {preference.evidence_count} reading choices "
            f"({round(preference.confidence * 100)}% confidence)."
        )

    return (
        DiscoveryPersonalizationSignal(
            preference_claim_id=preference.claim_id,
            key="format.preferred",
            value=preference.format,
            source=preference.source,
            confidence=max(0.0, min(1.0, preference.confidence)),
            evidence_count=max(0, preference.evidence_count),
            score_delta=score_delta,
            reason=reason,
        ),
    )


def _candidate_formats(candidate: DiscoveryCandidate) -> set[str]:
    values = [*candidate.formats, *(asset.format for asset in candidate.assets)]
    return {normalized for value in values if (normalized := _normalize_format(value))}


def _normalize_format(value: str) -> str:
    return " ".join(value.split()).upper()


__all__ = [
    "DiscoveryFormatPreference",
    "DiscoveryRankingProfile",
    "EXPLICIT_FORMAT_PREFERENCE_BOOST",
    "INFERRED_FORMAT_PREFERENCE_MAX_BOOST",
    "personalization_signals",
]
