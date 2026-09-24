from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.research import ResearchRepository, _fallback_websearch_query


async def _seed_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    chunks: list[str],
) -> tuple[LibraryEntry, Document, list[DocumentChunk]]:
    text = "\n".join(chunks)
    work = Work(
        canonical_title=f"Fallback Work {suffix}",
        normalized_title=f"fallback work {suffix}",
    )
    stored = StoredObject(
        sha256=(suffix.encode().hex() + "0" * 64)[:64],
        storage_key=f"objects/fallback/{suffix}",
        byte_size=len(text.encode("utf-8")),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Fallback Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/{suffix}.txt",
        stored_object_id=stored.id,
        byte_size=stored.byte_size,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="1",
        section_count=1,
        chunk_count=len(chunks),
    )
    session.add(document)
    await session.flush()

    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading=f"Fallback Section {suffix}",
        locator={"section": suffix},
        text=text,
    )
    session.add(section)
    await session.flush()

    records: list[DocumentChunk] = []
    cursor = 0
    for ordinal, chunk_text in enumerate(chunks):
        record = DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=ordinal,
            char_start=cursor,
            char_end=cursor + len(chunk_text),
            text=chunk_text,
        )
        session.add(record)
        records.append(record)
        cursor += len(chunk_text) + 1
    await session.flush()
    return entry, document, records


def test_fallback_query_is_bounded_normalized_and_syntax_safe() -> None:
    query = "Alpha alpha BETA gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"
    fallback = _fallback_websearch_query(query)

    assert fallback is not None
    assert fallback.split(" OR ") == [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "zeta",
        "eta",
        "theta",
        "iota",
        "kappa",
        "lambda",
        "mu",
    ]
    assert _fallback_websearch_query("single") is None
    assert _fallback_websearch_query('"exact phrase" other') is None
    assert _fallback_websearch_query("alpha OR beta") is None
    assert _fallback_websearch_query("alpha -beta") is None


async def test_strict_match_is_not_broadened_by_fallback(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="fallback-strict")
    session.add(principal)
    await session.flush()
    entry, _, chunks = await _seed_book(
        session,
        principal=principal,
        suffix="strict",
        chunks=[
            "Maritime trade crossed Atlantic routes.",
            "Maritime astronomy guided navigation.",
        ],
    )

    matches = await ResearchRepository(session).search_owned_passages(
        principal_id=principal.id,
        library_entry_ids=[entry.id],
        query="maritime trade",
        limit=10,
    )

    assert [match.chunk_id for match in matches] == [chunks[0].id]


async def test_zero_result_plain_query_uses_bounded_or_fallback(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="fallback-natural-language")
    session.add(principal)
    await session.flush()
    entry, _, chunks = await _seed_book(
        session,
        principal=principal,
        suffix="natural",
        chunks=[
            "Children were denied knowledge of their recorded birthday.",
            "Agricultural ledgers tracked harvest yields.",
        ],
    )

    matches = await ResearchRepository(session).search_owned_passages(
        principal_id=principal.id,
        library_entry_ids=[entry.id],
        query="enslaved children denied knowledge birthdays",
        limit=10,
    )

    assert matches
    assert matches[0].chunk_id == chunks[0].id
    assert matches[0].score > 0


async def test_fallback_remains_inside_selected_owned_documents(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="fallback-scope")
    session.add(principal)
    await session.flush()
    selected, _, selected_chunks = await _seed_book(
        session,
        principal=principal,
        suffix="scope-selected",
        chunks=["Children retained knowledge through oral memory."],
    )
    unselected, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="scope-unselected",
        chunks=["Enslaved children were denied knowledge of birthdays and records."],
    )

    matches = await ResearchRepository(session).search_owned_passages(
        principal_id=principal.id,
        library_entry_ids=[selected.id],
        query="enslaved children denied knowledge birthdays",
        limit=10,
    )

    assert unselected.id != selected.id
    assert [match.chunk_id for match in matches] == [selected_chunks[0].id]
    assert all(match.context.library_entry_id == selected.id for match in matches)


async def test_explicit_negative_search_syntax_never_falls_back(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="fallback-negative-syntax")
    session.add(principal)
    await session.flush()
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="negative",
        chunks=["Dogs appear in this passage, but cats do not."],
    )

    matches = await ResearchRepository(session).search_owned_passages(
        principal_id=principal.id,
        library_entry_ids=[entry.id],
        query="cats -dogs",
        limit=10,
    )

    assert matches == []


async def test_fallback_with_no_lexical_overlap_returns_empty(session: AsyncSession) -> None:
    principal = Principal(kind="local", external_subject="fallback-empty")
    session.add(principal)
    await session.flush()
    entry, _, _ = await _seed_book(
        session,
        principal=principal,
        suffix="empty",
        chunks=["Agricultural ledgers tracked harvest yields."],
    )

    matches = await ResearchRepository(session).search_owned_passages(
        principal_id=principal.id,
        library_entry_ids=[entry.id],
        query="celestial navigation stars position",
        limit=10,
    )

    assert matches == []
