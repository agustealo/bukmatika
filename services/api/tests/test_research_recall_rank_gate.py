from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from test_research_recall_public_domain import (
    PUBLIC_DOMAIN_WORKS,
    _scope,
    _seed_public_domain_book,
    _target_for_phrase,
)

from bukmatika.persistence.models import Principal
from bukmatika.research import ResearchRecallCase, ResearchRecallEvaluator, ResearchRecallSuite


async def test_postgres_fallback_ranks_representative_paraphrases_first(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = Principal(kind="local", external_subject="fallback-rank-gate")
    session.add(principal)
    await session.flush()

    books = {
        work.slug: await _seed_public_domain_book(
            session,
            principal=principal,
            work=work,
            tmp_path=tmp_path,
        )
        for work in PUBLIC_DOMAIN_WORKS
    }
    selected = [books[work.slug].entry.id for work in PUBLIC_DOMAIN_WORKS]
    cases = [
        ResearchRecallCase(
            case_id="paine-natural-paraphrase-rank",
            query="state authority exists because people are morally imperfect",
            library_entry_ids=selected,
            expected=[_target_for_phrase(books["common-sense"], "government by our wickedness")],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="douglass-natural-paraphrase-rank",
            query="enslaved children denied knowledge of their birthdays",
            library_entry_ids=selected,
            expected=[
                _target_for_phrase(
                    books["douglass-narrative"],
                    "slave who could tell of his birthday",
                )
            ],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="wollstonecraft-natural-paraphrase-rank",
            query="women need equal schooling to become rational partners",
            library_entry_ids=selected,
            expected=[_target_for_phrase(books["rights-of-woman"], "prepared by education")],
            limit=20,
        ),
        ResearchRecallCase(
            case_id="dubois-natural-paraphrase-rank",
            query="racial division defines the coming century",
            library_entry_ids=selected,
            expected=[
                _target_for_phrase(
                    books["souls-black-folk"],
                    "problem of the color line",
                )
            ],
            limit=20,
        ),
    ]

    result = await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(
        ResearchRecallSuite(
            principal_id=principal.id,
            cases=cases,
            minimum_case_recall=1.0,
            minimum_macro_recall=1.0,
        )
    )
    by_case = {case.case_id: case for case in result.cases}

    assert result.passed is True
    assert all(case.recall == 1.0 for case in result.cases)
    assert all(by_case[case.case_id].first_relevant_rank == 1 for case in cases)
    assert result.micro_recall == 1.0
    assert result.macro_recall == 1.0
    assert result.mean_reciprocal_rank == 1.0
