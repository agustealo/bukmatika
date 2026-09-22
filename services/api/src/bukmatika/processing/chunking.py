from bukmatika.processing.domain import ParsedChunk, ParsedSection


def chunk_sections(
    sections: tuple[ParsedSection, ...],
    *,
    max_chars: int = 1600,
    overlap_chars: int = 160,
) -> tuple[ParsedChunk, ...]:
    if max_chars < 128:
        raise ValueError("max_chars must be at least 128")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be between 0 and max_chars")

    chunks: list[ParsedChunk] = []
    ordinal = 0
    for section in sections:
        text = section.text
        start = 0
        while start < len(text):
            end = min(start + max_chars, len(text))
            if end < len(text):
                split = _preferred_split(text, start=start, end=end, max_chars=max_chars)
                if split > start:
                    end = split

            while start < end and text[start].isspace():
                start += 1
            while end > start and text[end - 1].isspace():
                end -= 1
            if start >= end:
                break

            chunks.append(
                ParsedChunk(
                    ordinal=ordinal,
                    section_ordinal=section.ordinal,
                    char_start=start,
                    char_end=end,
                    text=text[start:end],
                )
            )
            ordinal += 1
            if end >= len(text):
                break
            next_start = max(end - overlap_chars, start + 1)
            while next_start < len(text) and text[next_start].isspace():
                next_start += 1
            start = next_start

    if not chunks:
        raise ValueError("Document produced no searchable chunks")
    return tuple(chunks)


def _preferred_split(text: str, *, start: int, end: int, max_chars: int) -> int:
    floor = min(end, start + max_chars // 2)
    for separator in ("\n", ". ", "? ", "! ", "; ", ", ", " "):
        position = text.rfind(separator, floor, end)
        if position >= floor:
            return position + (1 if separator != " " else 0)
    return end
