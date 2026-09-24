import asyncio
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from bukmatika.ai.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingProviderNotReady,
    EmbeddingProviderRequestFailed,
    EmbeddingProviderResponseInvalid,
)
from bukmatika.ai.factory import build_embedding_gateway
from bukmatika.ai.gateway import ModelProviderIdentity
from bukmatika.config import Settings, get_settings
from bukmatika.persistence.models import Principal
from bukmatika.persistence.research import ResearchRepository
from bukmatika.research.recall_eval import (
    ResearchRecallCaseResult,
    ResearchRecallEvaluation,
    ResearchRecallEvaluator,
    ResearchRecallRetrievedPassage,
    ResearchRecallSuite,
    ResearchRecallSuiteInvalid,
    build_recall_case_result,
    build_recall_evaluation,
    validate_recall_case_targets,
)
from bukmatika.research.representative_benchmark import (
    seed_representative_public_domain_benchmark,
)
from bukmatika.research.semantic import ResearchSemanticIntegrityError, SemanticResearchService
from bukmatika.research.semantic_domain import ResearchSemanticSearchRequest

_PAINE_SEMANTIC_CASE_ID = "paine-natural-paraphrase"


class SemanticRecallCaseComparison(BaseModel):
    case_id: str
    lexical_recall: float = Field(ge=0.0, le=1.0)
    semantic_recall: float = Field(ge=0.0, le=1.0)
    lexical_first_relevant_rank: int | None = Field(default=None, ge=1)
    semantic_first_relevant_rank: int | None = Field(default=None, ge=1)
    lexical_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    semantic_reciprocal_rank: float = Field(ge=0.0, le=1.0)


class SemanticRecallBurnAcceptance(BaseModel):
    minimum_semantic_macro_recall: float = Field(default=1.0, ge=0.0, le=1.0)
    minimum_semantic_mrr: float = Field(default=11 / 12, ge=0.0, le=1.0)
    maximum_paine_first_relevant_rank: int = Field(default=5, ge=1, le=20)


class SemanticRecallBurnResult(BaseModel):
    provider: ModelProviderIdentity
    lexical: ResearchRecallEvaluation
    semantic: ResearchRecallEvaluation
    comparisons: list[SemanticRecallCaseComparison]
    recovered_case_ids: list[str]
    recall_regressed_case_ids: list[str]
    rank_regressed_case_ids: list[str]
    paine_recovered: bool
    acceptance: SemanticRecallBurnAcceptance
    passed: bool


class SemanticRecallEvaluator:
    """Measure explicit semantic retrieval with the canonical recall metric contract."""

    def __init__(
        self,
        *,
        service: SemanticResearchService,
        session: AsyncSession,
    ) -> None:
        self._service = service
        self._session = session

    async def evaluate(self, suite: ResearchRecallSuite) -> ResearchRecallEvaluation:
        repository = ResearchRepository(self._session)
        case_results: list[ResearchRecallCaseResult] = []
        for case in suite.cases:
            await validate_recall_case_targets(
                self._session,
                repository=repository,
                principal_id=suite.principal_id,
                case=case,
            )
            response = await self._service.search(
                principal_id=suite.principal_id,
                request=ResearchSemanticSearchRequest(
                    query=case.query,
                    library_entry_ids=case.library_entry_ids,
                    limit=case.limit,
                ),
            )
            case_results.append(
                build_recall_case_result(
                    case,
                    matches=[
                        ResearchRecallRetrievedPassage(
                            rank=index,
                            document_id=passage.document_id,
                            section_id=passage.section_id,
                            chunk_id=passage.chunk_id,
                            char_start=passage.char_start,
                            char_end=passage.char_end,
                            score=passage.score,
                        )
                        for index, passage in enumerate(response.passages, start=1)
                    ],
                    minimum_case_recall=suite.minimum_case_recall,
                )
            )
        return build_recall_evaluation(suite=suite, case_results=case_results)


def compare_retrieval_evaluations(
    *,
    provider: ModelProviderIdentity,
    lexical: ResearchRecallEvaluation,
    semantic: ResearchRecallEvaluation,
    acceptance: SemanticRecallBurnAcceptance | None = None,
) -> SemanticRecallBurnResult:
    criteria = acceptance or SemanticRecallBurnAcceptance()
    lexical_by_case = {case.case_id: case for case in lexical.cases}
    semantic_by_case = {case.case_id: case for case in semantic.cases}
    if set(lexical_by_case) != set(semantic_by_case):
        raise ResearchRecallSuiteInvalid("Lexical and semantic evaluations cover different cases")

    comparisons: list[SemanticRecallCaseComparison] = []
    recovered_case_ids: list[str] = []
    recall_regressed_case_ids: list[str] = []
    rank_regressed_case_ids: list[str] = []
    for case_id in lexical_by_case:
        lexical_case = lexical_by_case[case_id]
        semantic_case = semantic_by_case[case_id]
        comparisons.append(
            SemanticRecallCaseComparison(
                case_id=case_id,
                lexical_recall=lexical_case.recall,
                semantic_recall=semantic_case.recall,
                lexical_first_relevant_rank=lexical_case.first_relevant_rank,
                semantic_first_relevant_rank=semantic_case.first_relevant_rank,
                lexical_reciprocal_rank=lexical_case.reciprocal_rank,
                semantic_reciprocal_rank=semantic_case.reciprocal_rank,
            )
        )
        if semantic_case.recall > lexical_case.recall:
            recovered_case_ids.append(case_id)
        if semantic_case.recall < lexical_case.recall:
            recall_regressed_case_ids.append(case_id)
        if semantic_case.reciprocal_rank < lexical_case.reciprocal_rank:
            rank_regressed_case_ids.append(case_id)

    paine = semantic_by_case.get(_PAINE_SEMANTIC_CASE_ID)
    if paine is None:
        raise ResearchRecallSuiteInvalid("Representative semantic evaluation is missing the Paine case")
    paine_recovered = (
        paine.recall == 1.0
        and paine.first_relevant_rank is not None
        and paine.first_relevant_rank <= criteria.maximum_paine_first_relevant_rank
    )
    passed = (
        semantic.macro_recall >= criteria.minimum_semantic_macro_recall
        and semantic.mean_reciprocal_rank >= criteria.minimum_semantic_mrr
        and not recall_regressed_case_ids
        and paine_recovered
    )
    return SemanticRecallBurnResult(
        provider=provider,
        lexical=lexical,
        semantic=semantic,
        comparisons=comparisons,
        recovered_case_ids=recovered_case_ids,
        recall_regressed_case_ids=recall_regressed_case_ids,
        rank_regressed_case_ids=rank_regressed_case_ids,
        paine_recovered=paine_recovered,
        acceptance=criteria,
        passed=passed,
    )


async def run_semantic_recall_burn(
    *,
    settings: Settings,
    gateway: EmbeddingGateway | None = None,
) -> SemanticRecallBurnResult:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            embedding_gateway = gateway or build_embedding_gateway(settings=settings, client=client)
            readiness = await embedding_gateway.readiness()
            if not readiness.ready or readiness.identity is None:
                raise EmbeddingProviderNotReady(readiness)

            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(bind=connection, expire_on_commit=False)
                try:
                    principal = Principal(
                        kind="benchmark",
                        external_subject=f"semantic-recall-burn:{uuid4()}",
                    )
                    session.add(principal)
                    await session.flush()

                    @asynccontextmanager
                    async def same_session_scope() -> AsyncIterator[AsyncSession]:
                        yield session

                    with TemporaryDirectory(prefix="bukmatika-semantic-recall-") as scratch:
                        suite = await seed_representative_public_domain_benchmark(
                            session,
                            principal=principal,
                            scratch_dir=Path(scratch),
                        )
                        lexical = await ResearchRecallEvaluator(
                            session_scope_factory=same_session_scope
                        ).evaluate(suite)
                        semantic_service = SemanticResearchService(
                            gateway=embedding_gateway,
                            settings=settings,
                            session_scope_factory=same_session_scope,
                        )
                        semantic = await SemanticRecallEvaluator(
                            service=semantic_service,
                            session=session,
                        ).evaluate(suite)
                        result = compare_retrieval_evaluations(
                            provider=readiness.identity,
                            lexical=lexical,
                            semantic=semantic,
                        )
                finally:
                    if transaction.is_active:
                        await transaction.rollback()
                    await session.close()
                return result
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    try:
        result = asyncio.run(run_semantic_recall_burn(settings=settings))
    except (
        EmbeddingProviderNotReady,
        EmbeddingProviderRequestFailed,
        EmbeddingProviderResponseInvalid,
        ResearchRecallSuiteInvalid,
        ResearchSemanticIntegrityError,
    ) as exc:
        code = getattr(exc, "code", type(exc).__name__)
        print(
            json.dumps(
                {"status": "unavailable", "code": code, "message": str(exc)},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None

    print(result.model_dump_json(indent=2))
    if not result.passed:
        raise SystemExit(1)


__all__ = [
    "SemanticRecallBurnAcceptance",
    "SemanticRecallBurnResult",
    "SemanticRecallCaseComparison",
    "SemanticRecallEvaluator",
    "compare_retrieval_evaluations",
    "main",
    "run_semantic_recall_burn",
]
