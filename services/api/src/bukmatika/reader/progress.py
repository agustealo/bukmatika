from typing import Literal

ReadingStatus = Literal["reading", "finished"]


class CanonicalReaderProgressError(ValueError):
    pass


def canonical_progress_fraction(
    *,
    section_count: int,
    section_ordinal: int,
    section_text_length: int,
    char_offset: int,
) -> float:
    if section_count < 1 or section_ordinal < 0 or section_ordinal >= section_count:
        raise CanonicalReaderProgressError("Reader section ordinal is outside the document bounds")
    if section_text_length < 0 or char_offset < 0 or char_offset > section_text_length:
        raise CanonicalReaderProgressError("Reader character offset is outside the section text")

    if section_ordinal == section_count - 1 and char_offset == section_text_length:
        return 1.0

    within_section = 0.0 if section_text_length == 0 else char_offset / section_text_length
    progress = (section_ordinal + within_section) / section_count
    return min(1.0, max(0.0, progress))


def canonical_reading_status(progress_fraction: float) -> ReadingStatus:
    return "finished" if progress_fraction >= 1 else "reading"
