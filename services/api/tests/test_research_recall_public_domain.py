from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import Principal
from bukmatika.research.recall_eval import ResearchRecallEvaluator
from bukmatika.research.representative_benchmark import (
    seed_representative_public_domain_benchmark,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def test_public_domain_recall_improves_lexically_and_preserves_semantic_gap(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = Principal(kind="local", external_subject="public-domain-recall-benchmark")
    session.add(principal)
    await session.flush()
    suite = await seed_representative_public_domain_benchmark(
        session,
        principal=principal,
        scratch_dir=tmp_path,
    )

    result = await ResearchRecallEvaluator(session_scope_factory=_scope(session)).evaluate(suite)
    by_case = {case.case_id: case for case in result.cases}

    lexical_case_ids = {case.case_id for case in suite.cases[:8]}
    recovered_paraphrase_ids = {
        "douglass-natural-paraphrase",
        "wollstonecraft-natural-paraphrase",
        "dubois-natural-paraphrase",
    }

    assert result.passed is True
    assert all(by_case[case_id].recall == 1.0 for case_id in lexical_case_ids)
    assert all(by_case[case_id].first_relevant_rank == 1 for case_id in lexical_case_ids)
    assert by_case["paine-natural-paraphrase"].recall == 0.0
    assert by_case["paine-natural-paraphrase"].first_relevant_rank is None
    assert all(by_case[case_id].recall == 1.0 for case_id in recovered_paraphrase_ids)
    assert all(by_case[case_id].first_relevant_rank == 1 for case_id in recovered_paraphrase_ids)
    assert result.micro_recall == 11 / 12
    assert result.macro_recall == 11 / 12
    assert result.mean_reciprocal_rank == 11 / 12
