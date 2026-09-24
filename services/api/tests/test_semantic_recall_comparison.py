from uuid import uuid4

from bukmatika.ai.gateway import ModelProviderIdentity
from bukmatika.research.recall_eval import (
    ResearchRecallCaseResult,
    ResearchRecallEvaluation,
)
from bukmatika.research.semantic_recall_burn import compare_retrieval_evaluations


def _case(
    case_id: str,
    *,
    recall: float,
    rank: int | None,
) -> ResearchRecallCaseResult:
    return ResearchRecallCaseResult(
        case_id=case_id,
        query=case_id,
        expected_count=1,
        hit_count=1 if recall == 1.0 else 0,
        recall=recall,
        passed=True,
        first_relevant_rank=rank,
        reciprocal_rank=0.0 if rank is None else 1.0 / rank,
        missed_chunk_ids=[] if recall == 1.0 else [uuid4()],
        retrieved=[],
    )


def _evaluation(cases: list[ResearchRecallCaseResult]) -> ResearchRecallEvaluation:
    expected_count = len(cases)
    hit_count = sum(case.hit_count for case in cases)
    return ResearchRecallEvaluation(
        principal_id=uuid4(),
        case_count=len(cases),
        expected_count=expected_count,
        hit_count=hit_count,
        micro_recall=hit_count / expected_count,
        macro_recall=sum(case.recall for case in cases) / len(cases),
        mean_reciprocal_rank=sum(case.reciprocal_rank for case in cases) / len(cases),
        minimum_case_recall=0.0,
        minimum_macro_recall=0.0,
        passed=True,
        cases=cases,
    )


def test_comparison_surfaces_rank_regression_without_hiding_paine_recovery() -> None:
    case_ids = [
        "paine-natural-paraphrase",
        "douglass-natural-paraphrase",
        "wollstonecraft-natural-paraphrase",
        "dubois-natural-paraphrase",
    ]
    lexical = _evaluation(
        [
            _case(case_ids[0], recall=0.0, rank=None),
            _case(case_ids[1], recall=1.0, rank=1),
            _case(case_ids[2], recall=1.0, rank=1),
            _case(case_ids[3], recall=1.0, rank=1),
        ]
    )
    semantic = _evaluation(
        [
            _case(case_ids[0], recall=1.0, rank=2),
            _case(case_ids[1], recall=1.0, rank=3),
            _case(case_ids[2], recall=1.0, rank=1),
            _case(case_ids[3], recall=1.0, rank=1),
        ]
    )

    result = compare_retrieval_evaluations(
        provider=ModelProviderIdentity(
            provider="probe",
            model="semantic-v1",
            routing="local",
        ),
        lexical=lexical,
        semantic=semantic,
    )

    assert result.recovered_case_ids == ["paine-natural-paraphrase"]
    assert result.recall_regressed_case_ids == []
    assert result.rank_regressed_case_ids == ["douglass-natural-paraphrase"]
    assert result.paine_recovered is True
