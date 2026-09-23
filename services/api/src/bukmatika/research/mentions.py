import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from bukmatika.research.domain import (
    MAX_MENTION_ITEMS,
    ResearchEvidenceBundleResponse,
    ResearchMentionItem,
    ResearchMentionKind,
    ResearchMentionsResponse,
)

_WORD = r"[A-Z][\w'’\-]+"
_PERSON = re.compile(
    rf"\b(?P<cue>Mr|Mrs|Ms|Dr|Professor|Prof|Captain|Capt|King|Queen|President|Emperor|Empress|Pope|Saint|St)\.?(?:\s+)(?P<mention>{_WORD}(?:\s+{_WORD}){{0,3}})\b"
)
_PLACE_OF = re.compile(
    rf"\b(?P<cue>city|island|islands|kingdom|province|river|lake|mount|mountain|bay|gulf|peninsula|strait|harbor|harbour|port|fort)\s+of\s+(?P<mention>{_WORD}(?:\s+{_WORD}){{0,3}})\b",
    re.IGNORECASE,
)
_PLACE_SUFFIX = re.compile(
    rf"\b(?P<mention>{_WORD}(?:\s+{_WORD}){{0,3}}\s+(?P<cue>River|Sea|Ocean|Island|Islands|Bay|Gulf|Lake|Mount|Mountain|Mountains|Valley|Peninsula|Strait|Straits|Harbor|Harbour|Port|Fort|Province|Kingdom|Republic|Empire|City))\b"
)
_CONCEPT_OF = re.compile(
    r"\b(?P<cue>concept|principle|doctrine|theory|idea|practice|system|movement|method)\s+of\s+(?P<mention>[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){0,2})\b",
    re.IGNORECASE,
)
_CONCEPT_QUOTED = re.compile(
    r"\b(?P<cue>concept|term|principle|doctrine|theory|idea|practice|system|movement|method)\s+(?:called|known\s+as)\s+[\"“](?P<mention>[^\"”\n]{1,100})[\"”]",
    re.IGNORECASE,
)
_AMBIGUOUS_PROPER = re.compile(rf"\b(?P<mention>{_WORD}(?:\s+{_WORD}){{1,3}})\b")

_CONCEPT_TRAILING_STOP = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "which",
    "with",
}


@dataclass(frozen=True)
class _MentionCandidate:
    local_start: int
    local_end: int
    text: str
    normalized_text: str
    kind: ResearchMentionKind
    cue: str | None


_MentionParser = Callable[[re.Match[str]], _MentionCandidate | None]
_MergedMention = tuple[_MentionCandidate, list[str], UUID, UUID, int, int]


def build_mentions(bundle: ResearchEvidenceBundleResponse) -> ResearchMentionsResponse:
    merged: dict[tuple[UUID, UUID, int, int, str, ResearchMentionKind], _MergedMention] = {}

    for evidence in bundle.evidence:
        for candidate in _extract_candidates(evidence.text):
            source_start = evidence.char_start + candidate.local_start
            source_end = evidence.char_start + candidate.local_end
            key = (
                evidence.document_id,
                evidence.section_id,
                source_start,
                source_end,
                candidate.normalized_text,
                candidate.kind,
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = (
                    candidate,
                    [evidence.evidence_id],
                    evidence.document_id,
                    evidence.section_id,
                    source_start,
                    source_end,
                )
                continue

            existing_candidate, evidence_ids, document_id, section_id, _, _ = existing
            if evidence.evidence_id not in evidence_ids:
                evidence_ids.append(evidence.evidence_id)
            merged[key] = (
                existing_candidate,
                evidence_ids,
                document_id,
                section_id,
                source_start,
                source_end,
            )

    ordered = sorted(
        merged.values(),
        key=lambda value: (
            str(value[2]),
            str(value[3]),
            value[4],
            value[0].kind.value,
            value[0].normalized_text,
        ),
    )
    items = [
        ResearchMentionItem(
            mention_id=f"M{index}",
            text=candidate.text,
            normalized_text=candidate.normalized_text,
            kind=candidate.kind,
            cue=candidate.cue,
            evidence_ids=evidence_ids,
            document_id=document_id,
            section_id=section_id,
            source_char_start=source_start,
            source_char_end=source_end,
        )
        for index, (
            candidate,
            evidence_ids,
            document_id,
            section_id,
            source_start,
            source_end,
        ) in enumerate(ordered[:MAX_MENTION_ITEMS], start=1)
    ]
    return ResearchMentionsResponse(
        evidence=bundle,
        items=items,
        truncated=len(ordered) > MAX_MENTION_ITEMS,
    )


def _extract_candidates(text: str) -> list[_MentionCandidate]:
    candidates: list[_MentionCandidate] = []
    occupied: list[tuple[int, int]] = []

    def add(pattern: re.Pattern[str], parser: _MentionParser) -> None:
        for match in pattern.finditer(text):
            candidate = parser(match)
            if candidate is None:
                continue
            span = (candidate.local_start, candidate.local_end)
            if any(_overlaps(span, existing) for existing in occupied):
                continue
            candidates.append(candidate)
            occupied.append(span)

    add(_PERSON, _parse_person)
    add(_PLACE_OF, _parse_place)
    add(_PLACE_SUFFIX, _parse_place)
    add(_CONCEPT_QUOTED, _parse_concept)
    add(_CONCEPT_OF, _parse_concept)
    add(_AMBIGUOUS_PROPER, _parse_ambiguous)
    return sorted(candidates, key=lambda item: (item.local_start, item.local_end, item.kind.value))


def _candidate(
    match: re.Match[str],
    *,
    kind: ResearchMentionKind,
    cue: str | None,
    text: str | None = None,
    start: int | None = None,
    end: int | None = None,
) -> _MentionCandidate | None:
    mention = (text if text is not None else match.group("mention")).strip()
    if not mention:
        return None
    local_start = match.start("mention") if start is None else start
    local_end = match.end("mention") if end is None else end
    return _MentionCandidate(
        local_start=local_start,
        local_end=local_end,
        text=mention,
        normalized_text=" ".join(mention.casefold().split()),
        kind=kind,
        cue=cue,
    )


def _parse_person(match: re.Match[str]) -> _MentionCandidate | None:
    return _candidate(
        match,
        kind=ResearchMentionKind.PERSON,
        cue=match.group("cue").rstrip("."),
    )


def _parse_place(match: re.Match[str]) -> _MentionCandidate | None:
    return _candidate(
        match,
        kind=ResearchMentionKind.PLACE,
        cue=match.group("cue").casefold(),
    )


def _parse_concept(match: re.Match[str]) -> _MentionCandidate | None:
    mention = match.group("mention").strip()
    words = mention.split()
    while len(words) > 1 and words[-1].casefold() in _CONCEPT_TRAILING_STOP:
        words.pop()
    mention = " ".join(words).strip(" ,;:.")
    if not mention:
        return None
    relative = match.group("mention").find(mention)
    start = match.start("mention") + max(0, relative)
    return _candidate(
        match,
        kind=ResearchMentionKind.CONCEPT,
        cue=match.group("cue").casefold(),
        text=mention,
        start=start,
        end=start + len(mention),
    )


def _parse_ambiguous(match: re.Match[str]) -> _MentionCandidate | None:
    mention = match.group("mention")
    if mention.split()[0] in {"The", "This", "These", "Those", "Before", "After", "During"}:
        return None
    return _candidate(
        match,
        kind=ResearchMentionKind.AMBIGUOUS,
        cue=None,
    )


def _overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[0] < second[1] and second[0] < first[1]


__all__ = ["build_mentions"]
