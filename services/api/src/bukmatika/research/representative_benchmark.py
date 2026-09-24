from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, Edition, LibraryEntry, Principal, StoredObject, Work
from bukmatika.processing.chunking import chunk_sections
from bukmatika.processing.parsers import TextDocumentParser
from bukmatika.research.recall_eval import (
    ResearchRecallCase,
    ResearchRecallSuite,
    ResearchRecallSuiteInvalid,
    ResearchRecallTarget,
)


@dataclass(frozen=True, slots=True)
class RepresentativePublicDomainWork:
    slug: str
    title: str
    author: str
    publication_year: int
    source_url: str
    text: str


@dataclass(frozen=True, slots=True)
class SeededRepresentativeBook:
    entry: LibraryEntry
    document: Document
    chunks: tuple[DocumentChunk, ...]


REPRESENTATIVE_PUBLIC_DOMAIN_WORKS = (
    RepresentativePublicDomainWork(
        slug="common-sense",
        title="Common Sense",
        author="Thomas Paine",
        publication_year=1776,
        source_url="https://www.gutenberg.org/ebooks/147",
        text=(
            "Some writers have so confounded society with government, as to leave little or no "
            "distinction between them; whereas they are not only different, but have different "
            "origins. Society is produced by our wants, and government by our wickedness; the "
            "former promotes our happiness positively by uniting our affections, the latter "
            "negatively by restraining our vices. The one encourages intercourse, the other "
            "creates distinctions. The first a patron, the last a punisher.\n\n"
            "Society in every state is a blessing, but government even in its best state is but "
            "a necessary evil; in its worst state an intolerable one; for when we suffer, or are "
            "exposed to the same miseries by a government, which we might expect in a country "
            "without government, our calamity is heightened by reflecting that we furnish the "
            "means by which we suffer. Government, like dress, is the badge of lost innocence; "
            "the palaces of kings are built on the ruins of the bowers of paradise. For were the "
            "impulses of conscience clear, uniform, and irresistibly obeyed, man would need no "
            "other lawgiver; but that not being the case, he finds it necessary to surrender up "
            "a part of his property to furnish means for the protection of the rest; and this he "
            "is induced to do by the same prudence which in every other case advises him out of "
            "two evils to choose the least. Wherefore, security being the true design and end of "
            "government, it unanswerably follows that whatever form thereof appears most likely "
            "to ensure it to us, with the least expence and greatest benefit, is preferable to "
            "all others."
        ),
    ),
    RepresentativePublicDomainWork(
        slug="douglass-narrative",
        title="Narrative of the Life of Frederick Douglass, an American Slave",
        author="Frederick Douglass",
        publication_year=1845,
        source_url="https://www.gutenberg.org/ebooks/23",
        text=(
            "I was born in Tuckahoe, near Hillsborough, and about twelve miles from Easton, in "
            "Talbot county, Maryland. I have no accurate knowledge of my age, never having seen "
            "any authentic record containing it. By far the larger part of the slaves know as "
            "little of their ages as horses know of theirs, and it is the wish of most masters "
            "within my knowledge to keep their slaves thus ignorant. I do not remember to have "
            "ever met a slave who could tell of his birthday. They seldom come nearer to it than "
            "planting-time, harvest-time, cherry-time, spring-time, or fall-time. A want of "
            "information concerning my own was a source of unhappiness to me even during "
            "childhood. The white children could tell their ages. I could not tell why I ought "
            "to be deprived of the same privilege. I was not allowed to make any inquiries of my "
            "master concerning it. He deemed all such inquiries on the part of a slave improper "
            "and impertinent, and evidence of a restless spirit."
        ),
    ),
    RepresentativePublicDomainWork(
        slug="rights-of-woman",
        title="A Vindication of the Rights of Woman",
        author="Mary Wollstonecraft",
        publication_year=1792,
        source_url="https://www.gutenberg.org/ebooks/3420",
        text=(
            "Contending for the rights of women, my main argument is built on this simple "
            "principle, that if she be not prepared by education to become the companion of man, "
            "she will stop the progress of knowledge, for truth must be common to all, or it will "
            "be inefficacious with respect to its influence on general practice. And how can "
            "woman be expected to co-operate, unless she know why she ought to be virtuous? "
            "Unless freedom strengthen her reason till she comprehend her duty, and see in what "
            "manner it is connected with her real good?\n\n"
            "Consequently the perfection of our nature and capability of happiness, must be "
            "estimated by the degree of reason, virtue, and knowledge, that distinguish the "
            "individual, and direct the laws which bind society: and that from the exercise of "
            "reason, knowledge and virtue naturally flow, is equally undeniable, if mankind be "
            "viewed collectively."
        ),
    ),
    RepresentativePublicDomainWork(
        slug="souls-black-folk",
        title="The Souls of Black Folk",
        author="W. E. B. Du Bois",
        publication_year=1903,
        source_url="https://www.gutenberg.org/ebooks/408",
        text=(
            "Herein lie buried many things which if read with patience may show the strange "
            "meaning of being black here at the dawning of the Twentieth Century. This meaning "
            "is not without interest to you, Gentle Reader; for the problem of the Twentieth "
            "Century is the problem of the color line.\n\n"
            "The problem of the twentieth century is the problem of the color-line,—the relation "
            "of the darker to the lighter races of men in Asia and Africa, in America and the "
            "islands of the sea. It was a phase of this problem that caused the Civil War; and "
            "however much they who marched South and North in 1861 may have fixed on the "
            "technical points, of union and local autonomy as a shibboleth, all nevertheless "
            "knew, as we know, that the question of Negro slavery was the real cause of the "
            "conflict."
        ),
    ),
)


async def seed_representative_public_domain_benchmark(
    session: AsyncSession,
    *,
    principal: Principal,
    scratch_dir: Path,
) -> ResearchRecallSuite:
    books = {
        work.slug: await _seed_book(
            session,
            principal=principal,
            work=work,
            scratch_dir=scratch_dir,
        )
        for work in REPRESENTATIVE_PUBLIC_DOMAIN_WORKS
    }
    selected = [books[work.slug].entry.id for work in REPRESENTATIVE_PUBLIC_DOMAIN_WORKS]
    cases = _build_cases(books=books, selected_library_entry_ids=selected)
    return ResearchRecallSuite(
        principal_id=principal.id,
        cases=cases,
        minimum_case_recall=0.0,
        minimum_macro_recall=11 / 12,
    )


async def _seed_book(
    session: AsyncSession,
    *,
    principal: Principal,
    work: RepresentativePublicDomainWork,
    scratch_dir: Path,
) -> SeededRepresentativeBook:
    source_path = scratch_dir / f"{work.slug}.txt"
    source_path.write_text(work.text, encoding="utf-8")
    parser = TextDocumentParser()
    parsed = parser.parse(source_path, max_bytes=256_000)
    parsed_chunks = chunk_sections(parsed.sections)

    work_record = Work(
        canonical_title=work.title,
        normalized_title=work.title.casefold(),
    )
    source_bytes = work.text.encode("utf-8")
    source_sha = sha256(source_bytes).hexdigest()
    stored = await session.scalar(select(StoredObject).where(StoredObject.sha256 == source_sha))
    if stored is None:
        stored = StoredObject(
            sha256=source_sha,
            storage_key=f"objects/recall-public-domain/{source_sha}",
            byte_size=len(source_bytes),
            media_type="text/plain",
        )
        session.add(stored)
    session.add(work_record)
    await session.flush()

    edition = Edition(
        work_id=work_record.id,
        title=work.title,
        language="en",
        publication_year=work.publication_year,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=work.source_url,
        stored_object_id=stored.id,
        byte_size=stored.byte_size,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work_record.id,
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
        parser_name=parsed.parser_name,
        parser_version=parsed.parser_version,
        section_count=len(parsed.sections),
        chunk_count=len(parsed_chunks),
    )
    session.add(document)
    await session.flush()

    sections_by_ordinal: dict[int, DocumentSection] = {}
    for parsed_section in parsed.sections:
        section = DocumentSection(
            document_id=document.id,
            ordinal=parsed_section.ordinal,
            heading=parsed_section.heading,
            locator=parsed_section.locator,
            text=parsed_section.text,
        )
        session.add(section)
        sections_by_ordinal[parsed_section.ordinal] = section
    await session.flush()

    chunks: list[DocumentChunk] = []
    for parsed_chunk in parsed_chunks:
        chunk = DocumentChunk(
            document_id=document.id,
            section_id=sections_by_ordinal[parsed_chunk.section_ordinal].id,
            ordinal=parsed_chunk.ordinal,
            char_start=parsed_chunk.char_start,
            char_end=parsed_chunk.char_end,
            text=parsed_chunk.text,
        )
        session.add(chunk)
        chunks.append(chunk)
    await session.flush()
    return SeededRepresentativeBook(entry=entry, document=document, chunks=tuple(chunks))


def _target_for_phrase(book: SeededRepresentativeBook, phrase: str) -> ResearchRecallTarget:
    matches = [chunk for chunk in book.chunks if phrase.casefold() in chunk.text.casefold()]
    if len(matches) != 1:
        raise ResearchRecallSuiteInvalid(
            f"Representative benchmark phrase must resolve to exactly one chunk: {phrase!r}"
        )
    chunk = matches[0]
    return ResearchRecallTarget(
        document_id=book.document.id,
        section_id=chunk.section_id,
        chunk_id=chunk.id,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
    )


def _build_cases(
    *,
    books: dict[str, SeededRepresentativeBook],
    selected_library_entry_ids: list[UUID],
) -> list[ResearchRecallCase]:
    return [
        ResearchRecallCase(
            case_id="paine-necessary-evil",
            query="government necessary evil",
            library_entry_ids=selected_library_entry_ids,
            expected=[_target_for_phrase(books["common-sense"], "necessary evil")],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="paine-security-purpose",
            query="security design end government",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(books["common-sense"], "security being the true design")
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="douglass-birthday-age",
            query="slaves birthday age",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(
                    books["douglass-narrative"],
                    "slave who could tell of his birthday",
                )
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="douglass-childhood-privilege",
            query="white children ages privilege",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(
                    books["douglass-narrative"],
                    "white children could tell their ages",
                )
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="wollstonecraft-education-companion",
            query="education companion man truth common",
            library_entry_ids=selected_library_entry_ids,
            expected=[_target_for_phrase(books["rights-of-woman"], "prepared by education")],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="wollstonecraft-reason-virtue-knowledge",
            query="reason virtue knowledge",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(
                    books["rights-of-woman"],
                    "degree of reason, virtue, and knowledge",
                )
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="dubois-color-line",
            query="problem twentieth century color line",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(books["souls-black-folk"], "problem of the color line")
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="dubois-races-relation",
            query="relation darker lighter races",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(
                    books["souls-black-folk"],
                    "relation of the darker to the lighter races",
                )
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="paine-natural-paraphrase",
            query="state authority exists because people are morally imperfect",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(books["common-sense"], "government by our wickedness")
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="douglass-natural-paraphrase",
            query="enslaved children denied knowledge of their birthdays",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(
                    books["douglass-narrative"],
                    "slave who could tell of his birthday",
                )
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="wollstonecraft-natural-paraphrase",
            query="women need equal schooling to become rational partners",
            library_entry_ids=selected_library_entry_ids,
            expected=[_target_for_phrase(books["rights-of-woman"], "prepared by education")],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="dubois-natural-paraphrase",
            query="racial division defines the coming century",
            library_entry_ids=selected_library_entry_ids,
            expected=[
                _target_for_phrase(books["souls-black-folk"], "problem of the color line")
            ],
            limit=20,
        ),
    ]


__all__ = [
    "REPRESENTATIVE_PUBLIC_DOMAIN_WORKS",
    "RepresentativePublicDomainWork",
    "SeededRepresentativeBook",
    "seed_representative_public_domain_benchmark",
]
