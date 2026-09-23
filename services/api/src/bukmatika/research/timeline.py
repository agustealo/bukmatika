import calendar
import re
from dataclasses import dataclass
from uuid import UUID

from bukmatika.research.domain import (
    MAX_TIMELINE_ITEMS,
    ResearchEvidenceBundleResponse,
    ResearchTimelineItem,
    ResearchTimelinePrecision,
    ResearchTimelineResponse,
)

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH_PATTERN = "|".join(_MONTHS)
_MONTH_FIRST = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*|\s+)(?P<year>[1-9]\d{{2,3}})(?:\s+(?P<era>BCE|BC|CE|AD))?\b",
    re.IGNORECASE,
)
_DAY_FIRST = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_PATTERN})\s+(?P<year>[1-9]\d{{2,3}})(?:\s+(?P<era>BCE|BC|CE|AD))?\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(
    r"\b(?P<year>[1-9]\d{3})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"
)
_NUMERIC_DATE = re.compile(
    r"\b(?P<first>\d{1,2})[/-](?P<second>\d{1,2})[/-](?P<year>[1-9]\d{3})\b"
)
_MONTH_YEAR = re.compile(
    rf"\b(?P<month>{_MONTH_PATTERN})\s+(?P<year>[1-9]\d{{2,3}})(?:\s+(?P<era>BCE|BC|CE|AD))?\b",
    re.IGNORECASE,
)
_ERA_PREFIX = re.compile(r"\b(?P<era>AD|CE|BC|BCE)\s+(?P<year>[1-9]\d{0,3})\b", re.IGNORECASE)
_ERA_SUFFIX = re.compile(r"\b(?P<year>[1-9]\d{0,3})\s+(?P<era>BCE|BC|CE|AD)\b", re.IGNORECASE)
_BARE_YEAR = re.compile(r"(?<![\d/-])(?P<year>1\d{3}|20\d{2})(?![\d/-])")
_SENTENCE_BREAK = re.compile(r"[.!?](?:\s|$)")


@dataclass(frozen=True)
class _TimelineCandidate:
    local_start: int
    local_end: int
    date_label: str
    year: int
    month: int | None
    day: int | None
    era: str | None
    precision: ResearchTimelinePrecision


def build_timeline(bundle: ResearchEvidenceBundleResponse) -> ResearchTimelineResponse:
    merged: dict[
        tuple[UUID, UUID, int, int, str],
        tuple[_TimelineCandidate, str, list[str], UUID, UUID, int, int],
    ] = {}
    dated_evidence_ids: set[str] = set()
    total_candidates = 0

    for evidence in bundle.evidence:
        candidates = _extract_candidates(evidence.text)
        if candidates:
            dated_evidence_ids.add(evidence.evidence_id)
        total_candidates += len(candidates)
        for candidate in candidates:
            source_start = evidence.char_start + candidate.local_start
            source_end = evidence.char_start + candidate.local_end
            key = (
                evidence.document_id,
                evidence.section_id,
                source_start,
                source_end,
                candidate.date_label.casefold(),
            )
            event_text = _source_excerpt(
                evidence.text,
                candidate.local_start,
                candidate.local_end,
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = (
                    candidate,
                    event_text,
                    [evidence.evidence_id],
                    evidence.document_id,
                    evidence.section_id,
                    source_start,
                    source_end,
                )
                continue
            existing_candidate, existing_text, evidence_ids, document_id, section_id, _, _ = existing
            if evidence.evidence_id not in evidence_ids:
                evidence_ids.append(evidence.evidence_id)
            merged[key] = (
                existing_candidate,
                event_text if len(event_text) > len(existing_text) else existing_text,
                evidence_ids,
                document_id,
                section_id,
                source_start,
                source_end,
            )

    ordered = sorted(merged.values(), key=_timeline_sort_key)
    truncated = len(ordered) > MAX_TIMELINE_ITEMS
    items = [
        ResearchTimelineItem(
            timeline_id=f"T{index}",
            date_label=candidate.date_label,
            year=candidate.year,
            month=candidate.month,
            day=candidate.day,
            era=candidate.era,
            precision=candidate.precision,
            event_text=event_text,
            evidence_ids=evidence_ids,
            document_id=document_id,
            section_id=section_id,
            source_char_start=source_start,
            source_char_end=source_end,
        )
        for index, (
            candidate,
            event_text,
            evidence_ids,
            document_id,
            section_id,
            source_start,
            source_end,
        ) in enumerate(ordered[:MAX_TIMELINE_ITEMS], start=1)
    ]
    undated = [
        evidence.evidence_id
        for evidence in bundle.evidence
        if evidence.evidence_id not in dated_evidence_ids
    ]
    return ResearchTimelineResponse(
        evidence=bundle,
        items=items,
        undated_evidence_ids=undated,
        truncated=truncated or total_candidates > len(merged) + MAX_TIMELINE_ITEMS,
    )


def _extract_candidates(text: str) -> list[_TimelineCandidate]:
    candidates: list[_TimelineCandidate] = []
    occupied: list[tuple[int, int]] = []

    def add(pattern: re.Pattern[str], parser: object) -> None:
        parse = parser
        for match in pattern.finditer(text):
            span = match.span()
            if any(_overlaps(span, existing) for existing in occupied):
                continue
            candidate = parse(match)  # type: ignore[operator]
            if candidate is None:
                continue
            candidates.append(candidate)
            occupied.append(span)

    add(_MONTH_FIRST, _parse_named_day)
    add(_DAY_FIRST, _parse_named_day)
    add(_ISO_DATE, _parse_iso_date)
    add(_NUMERIC_DATE, _parse_numeric_date)
    add(_MONTH_YEAR, _parse_month_year)
    add(_ERA_PREFIX, _parse_era_year)
    add(_ERA_SUFFIX, _parse_era_year)
    add(_BARE_YEAR, _parse_bare_year)
    return sorted(candidates, key=lambda item: item.local_start)


def _parse_named_day(match: re.Match[str]) -> _TimelineCandidate | None:
    year = int(match.group("year"))
    month = _MONTHS[match.group("month").casefold()]
    day = int(match.group("day"))
    if not _valid_day(year, month, day):
        return None
    return _candidate(
        match,
        year=year,
        month=month,
        day=day,
        era=_normalize_era(match.groupdict().get("era")),
        precision=ResearchTimelinePrecision.DAY,
    )


def _parse_iso_date(match: re.Match[str]) -> _TimelineCandidate | None:
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    if not _valid_day(year, month, day):
        return None
    return _candidate(
        match,
        year=year,
        month=month,
        day=day,
        era=None,
        precision=ResearchTimelinePrecision.DAY,
    )


def _parse_numeric_date(match: re.Match[str]) -> _TimelineCandidate | None:
    first = int(match.group("first"))
    second = int(match.group("second"))
    year = int(match.group("year"))
    if first > 31 or second > 31 or first == 0 or second == 0:
        return None
    if first > 12 and second <= 12 and _valid_day(year, second, first):
        return _candidate(
            match,
            year=year,
            month=second,
            day=first,
            era=None,
            precision=ResearchTimelinePrecision.DAY,
        )
    if second > 12 and first <= 12 and _valid_day(year, first, second):
        return _candidate(
            match,
            year=year,
            month=first,
            day=second,
            era=None,
            precision=ResearchTimelinePrecision.DAY,
        )
    if first <= 12 and second <= 12:
        return _candidate(
            match,
            year=year,
            month=None,
            day=None,
            era=None,
            precision=ResearchTimelinePrecision.AMBIGUOUS,
        )
    return None


def _parse_month_year(match: re.Match[str]) -> _TimelineCandidate:
    return _candidate(
        match,
        year=int(match.group("year")),
        month=_MONTHS[match.group("month").casefold()],
        day=None,
        era=_normalize_era(match.groupdict().get("era")),
        precision=ResearchTimelinePrecision.MONTH,
    )


def _parse_era_year(match: re.Match[str]) -> _TimelineCandidate:
    return _candidate(
        match,
        year=int(match.group("year")),
        month=None,
        day=None,
        era=_normalize_era(match.group("era")),
        precision=ResearchTimelinePrecision.YEAR,
    )


def _parse_bare_year(match: re.Match[str]) -> _TimelineCandidate:
    return _candidate(
        match,
        year=int(match.group("year")),
        month=None,
        day=None,
        era=None,
        precision=ResearchTimelinePrecision.YEAR,
    )


def _candidate(
    match: re.Match[str],
    *,
    year: int,
    month: int | None,
    day: int | None,
    era: str | None,
    precision: ResearchTimelinePrecision,
) -> _TimelineCandidate:
    return _TimelineCandidate(
        local_start=match.start(),
        local_end=match.end(),
        date_label=match.group(0),
        year=year,
        month=month,
        day=day,
        era=era,
        precision=precision,
    )


def _valid_day(year: int, month: int, day: int) -> bool:
    if not 1 <= month <= 12 or day < 1:
        return False
    try:
        return day <= calendar.monthrange(max(1, year), month)[1]
    except calendar.IllegalMonthError:
        return False


def _normalize_era(value: str | None) -> str | None:
    if value is None:
        return None
    return "BCE" if value.casefold() in {"bc", "bce"} else "CE"


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


def _source_excerpt(text: str, start: int, end: int) -> str:
    sentence_start = 0
    for match in _SENTENCE_BREAK.finditer(text, 0, start):
        sentence_start = match.end()
    sentence_end = len(text)
    next_break = _SENTENCE_BREAK.search(text, end)
    if next_break is not None:
        sentence_end = next_break.end()
    sentence = text[sentence_start:sentence_end].strip()
    if len(sentence) <= 500:
        return sentence

    midpoint = (start + end) // 2
    excerpt_start = max(sentence_start, midpoint - 250)
    excerpt_end = min(sentence_end, excerpt_start + 500)
    if excerpt_end - excerpt_start < 500:
        excerpt_start = max(sentence_start, excerpt_end - 500)
    return text[excerpt_start:excerpt_end].strip()


def _timeline_sort_key(
    value: tuple[_TimelineCandidate, str, list[str], UUID, UUID, int, int],
) -> tuple[int, int, int, int, int, int]:
    candidate, _, _, _, _, source_start, _ = value
    signed_year = -candidate.year if candidate.era == "BCE" else candidate.year
    ambiguity = 1 if candidate.precision is ResearchTimelinePrecision.AMBIGUOUS else 0
    return (
        signed_year,
        candidate.month or 0,
        candidate.day or 0,
        ambiguity,
        source_start,
        candidate.local_start,
    )


__all__ = ["build_timeline"]
