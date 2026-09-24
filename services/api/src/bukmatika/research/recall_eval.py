import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from bukmatika.config import get_settings
from bukmatika.persistence.document_models import DocumentChunk, DocumentSection
from bukmatika.persistence.research import (
    ResearchRepository,
    ResearchSelectionDenied,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ResearchRecallSuiteInvalid(ValueError):
    code = "RESEARCH_RECALL_SUITE_INVALID"


class ResearchRecallTarget(BaseModel):
    document_id: UUID
    section_id: UUID
    chunk_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_coordinates(self) -> "ResearchRecallTarget":
        if self.char_end <= self.char_start:
            raise ValueError("Recall target coordinates must have positive length")
        return self


class ResearchRecallCase(BaseModel):
    case_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    query: str = Field(min_length=1, max_length=200)
    library_entry_ids: list[UUID] = Field(min_length=1, max_length=20)
    expected: list[ResearchRecallTarget] = Field(min_length=1, max_length=50)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Recall query cannot be blank")
        return normalized

    @field_validator("library_entry_ids")
    @classmethod
    def require_unique_entries(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("Recall case library entries must be unique")
        return value

    @field_validator("expected")
    @classmethod
    def require_unique_targets(
        cls,
        value: list[ResearchRecallTarget],
    ) -> list[ResearchRecallTarget]:
        chunk_ids = [target.chunk_id for target in value]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise ValueError("Recall case expected chunk IDs must be unique")
        return value


class ResearchRecallSuite(BaseModel):
    principal_id: UUID
    cases: list[ResearchRecallCase] = Field(min_length=1, max_length=200)
    minimum_case_recall: float = Field(ge=0.0, le=1.0)
    minimum_macro_recall: float = Field(ge=0.0, le=1.0)

    @field_validator("cases")
    @classmethod
    def require_unique_case_ids(cls, value: list[ResearchRecallCase]) -> list[ResearchRecallCase]:
        case_ids = [case.case_id for case in value]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("Recall suite case IDs must be unique")
        return value


class ResearchRecallRetrievedPassage(BaseModel):
    rank: int = Field(ge=1)
    document_id: UUID
    section_id: UUID
    chunk_id: UUID
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=1)
    score: float = Field(ge=0.0)


class ResearchRecallCaseResult(BaseModel):
    case_id: str
    query: str
    expected_count: int = Field(ge=1)
    hit_count: int = Field(ge=0)
    recall: float = Field(ge=0.0, le=1.0)
    passed: bool
    first_relevant_rank: int | None = Field(default=None, ge=1)
    reciprocal_rank: float = Field(ge=0.0, le=1.0)
    missed_chunk_ids: list[UUID]
    retrieved: list[ResearchRecallRetrievedPassage]


class ResearchRecallEvaluation(BaseModel):
    principal_id: UUID
    case_count: int = Field(ge=1)
    expected_count: int = Field(ge=1)
    hit_count: int = Field(ge=0)
    micro_recall: float = Field(ge=0.0, le=1.0)
    macro_recall: float = Field(ge=0.0, le=1.0)
    mean_reciprocal_rank: float = Field(ge=0.0, le=1.0)
    minimum_case_recall: float = Field(ge=0.0, le=1.0)
    minimum_macro_recall: float = Field(ge=0.0, le=1.0)
    passed: bool
    cases: list[ResearchRecallCaseResult] = Field(min_length=1)


class ResearchRecallEvaluator:
    """Read-only recall measurement over the canonical PostgreSQL lexical retrieval path."""

    def __init__(self, *, session_scope_factory: SessionScopeFactory) -> None:
        self._session_scope = session_scope_factory

    async def evaluate(self, suite: ResearchRecallSuite) -> ResearchRecallEvaluation:
        async with self._session_scope() as database_session:
            repository = ResearchRepository(database_session)
            case_results: list[ResearchRecallCaseResult] = []

            for case in suite.cases:
                await _validate_expected_targets(
                    database_session,
                    repository=repository,
                    principal_id=suite.principal_id,
                    case=case,
                )
                matches = await repository.search_owned_passages(
                    principal_id=suite.principal_id,
                    library_entry_ids=case.library_entry_ids,
                    query=case.query,
                    limit=case.limit,
                )
                case_results.append(
                    _case_result(
                        case,
                        matches=[
                            ResearchRecallRetrievedPassage(
                                rank=index,
                                document_id=match.context.document_id,
                                section_id=match.section_id,
                                chunk_id=match.chunk_id,
                                char_start=match.char_start,
                                char_end=match.char_end,
                                score=max(0.0, match.score),
                            )
                            for index, match in enumerate(matches, start=1)
                        ],
                        minimum_case_recall=suite.minimum_case_recall,
                    )
                )

        expected_count = sum(result.expected_count for result in case_results)
        hit_count = sum(result.hit_count for result in case_results)
        micro_recall = hit_count / expected_count
        macro_recall = sum(result.recall for result in case_results) / len(case_results)
        mean_reciprocal_rank = (
            sum(result.reciprocal_rank for result in case_results) / len(case_results)
        )
        passed = (
            all(result.passed for result in case_results)
            and macro_recall >= suite.minimum_macro_recall
        )

        return ResearchRecallEvaluation(
            principal_id=suite.principal_id,
            case_count=len(case_results),
            expected_count=expected_count,
            hit_count=hit_count,
            micro_recall=micro_recall,
            macro_recall=macro_recall,
            mean_reciprocal_rank=mean_reciprocal_rank,
            minimum_case_recall=suite.minimum_case_recall,
            minimum_macro_recall=suite.minimum_macro_recall,
            passed=passed,
            cases=case_results,
        )


async def _validate_expected_targets(
    session: AsyncSession,
    *,
    repository: ResearchRepository,
    principal_id: UUID,
    case: ResearchRecallCase,
) -> None:
    try:
        contexts = await repository.document_contexts(
            principal_id=principal_id,
            library_entry_ids=case.library_entry_ids,
        )
    except ResearchSelectionDenied as exc:
        raise ResearchRecallSuiteInvalid(
            f"Recall case {case.case_id!r} selects a library entry unavailable to this principal"
        ) from exc

    selected_document_ids = {context.document_id for context in contexts}
    expected_by_chunk = {target.chunk_id: target for target in case.expected}

    rows = (
        await session.execute(
            select(DocumentChunk, DocumentSection)
            .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
            .where(DocumentChunk.id.in_(expected_by_chunk))
        )
    ).all()
    row_by_chunk = {chunk.id: (chunk, section) for chunk, section in rows}

    for target in case.expected:
        row = row_by_chunk.get(target.chunk_id)
        if row is None:
            raise ResearchRecallSuiteInvalid(
                f"Recall case {case.case_id!r} references a missing canonical chunk"
            )
        chunk, section = row
        if chunk.document_id not in selected_document_ids:
            raise ResearchRecallSuiteInvalid(
                f"Recall case {case.case_id!r} expected chunk is outside the selected owned books"
            )
        if (
            chunk.document_id != target.document_id
            or section.id != target.section_id
            or chunk.section_id != target.section_id
            or chunk.char_start != target.char_start
            or chunk.char_end != target.char_end
        ):
            raise ResearchRecallSuiteInvalid(
                f"Recall case {case.case_id!r} expected coordinates do not match canonical data"
            )


def _case_result(
    case: ResearchRecallCase,
    *,
    matches: list[ResearchRecallRetrievedPassage],
    minimum_case_recall: float,
) -> ResearchRecallCaseResult:
    expected_ids = {target.chunk_id for target in case.expected}
    ranks = {match.chunk_id: match.rank for match in matches}
    hit_ids = expected_ids.intersection(ranks)
    missed = [target.chunk_id for target in case.expected if target.chunk_id not in hit_ids]
    first_relevant_rank = min((ranks[chunk_id] for chunk_id in hit_ids), default=None)
    recall = len(hit_ids) / len(expected_ids)
    reciprocal_rank = 0.0 if first_relevant_rank is None else 1.0 / first_relevant_rank
    return ResearchRecallCaseResult(
        case_id=case.case_id,
        query=case.query,
        expected_count=len(expected_ids),
        hit_count=len(hit_ids),
        recall=recall,
        passed=recall >= minimum_case_recall,
        first_relevant_rank=first_relevant_rank,
        reciprocal_rank=reciprocal_rank,
        missed_chunk_ids=missed,
        retrieved=matches,
    )


def _scope_for_engine(engine: AsyncEngine) -> SessionScopeFactory:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(bind=engine, expire_on_commit=False) as session:
            yield session

    return scope


def _load_suite(path: Path) -> ResearchRecallSuite:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchRecallSuiteInvalid("Recall suite file could not be read as JSON") from exc
    try:
        return ResearchRecallSuite.model_validate(payload)
    except ValidationError as exc:
        raise ResearchRecallSuiteInvalid("Recall suite JSON failed schema validation") from exc


async def _run_from_settings(path: Path) -> ResearchRecallEvaluation:
    suite = _load_suite(path)
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        return await ResearchRecallEvaluator(
            session_scope_factory=_scope_for_engine(engine)
        ).evaluate(suite)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bukmatika-research-recall-eval",
        description=(
            "Measure canonical PostgreSQL lexical retrieval recall against explicit evidence targets."
        ),
    )
    parser.add_argument("suite", type=Path, help="Path to a recall-suite JSON file")
    args = parser.parse_args()

    try:
        result = asyncio.run(_run_from_settings(args.suite))
    except ResearchRecallSuiteInvalid as exc:
        print(
            json.dumps({"status": "invalid", "code": exc.code, "message": str(exc)}),
            file=sys.stderr,
            sort_keys=True,
        )
        raise SystemExit(2) from None

    print(result.model_dump_json(indent=2))
    if not result.passed:
        raise SystemExit(1)


__all__ = [
    "ResearchRecallCase",
    "ResearchRecallCaseResult",
    "ResearchRecallEvaluation",
    "ResearchRecallEvaluator",
    "ResearchRecallRetrievedPassage",
    "ResearchRecallSuite",
    "ResearchRecallSuiteInvalid",
    "ResearchRecallTarget",
    "main",
]
