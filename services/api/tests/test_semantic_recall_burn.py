import os
from typing import Final

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from bukmatika.ai.embedding_gateway import EmbeddingBatch, EmbeddingRequest
from bukmatika.ai.gateway import (
    ModelProviderIdentity,
    ModelProviderReadiness,
    ModelReadinessState,
)
from bukmatika.config import Settings
from bukmatika.persistence.models import Principal
from bukmatika.research.semantic_recall_burn import run_semantic_recall_burn

_CASE_RELATIONSHIPS: Final = (
    ("government necessary evil", "necessary evil"),
    ("security design end government", "security being the true design"),
    ("slaves birthday age", "slave who could tell of his birthday"),
    ("white children ages privilege", "white children could tell their ages"),
    ("education companion man truth common", "prepared by education"),
    ("reason virtue knowledge", "degree of reason, virtue, and knowledge"),
    ("problem twentieth century color line", "problem of the color line"),
    ("relation darker lighter races", "relation of the darker to the lighter races"),
    (
        "state authority exists because people are morally imperfect",
        "government by our wickedness",
    ),
    (
        "enslaved children denied knowledge of their birthdays",
        "slave who could tell of his birthday",
    ),
    (
        "women need equal schooling to become rational partners",
        "prepared by education",
    ),
    (
        "racial division defines the coming century",
        "problem of the color line",
    ),
)


class _RepresentativeSemanticGateway:
    def __init__(self) -> None:
        self._identity = ModelProviderIdentity(
            provider="benchmark-probe",
            model="representative-semantic-v1",
            routing="local",
        )

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        return ModelProviderReadiness(
            state=ModelReadinessState.READY,
            configured=True,
            ready=True,
            identity=self._identity,
        )

    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        return EmbeddingBatch(
            identity=self._identity,
            vectors=[_vector_for_text(value) for value in request.inputs],
        )


def _vector_for_text(value: str) -> list[float]:
    normalized = value.casefold()
    vector = [0.0] * (len(_CASE_RELATIONSHIPS) + 1)
    for index, (query, target_phrase) in enumerate(_CASE_RELATIONSHIPS):
        if normalized == query or target_phrase in normalized:
            vector[index] = 1.0
    vector[-1] = 0.01
    return vector


async def _benchmark_principal_count(database_url: str) -> int:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                select(func.count(Principal.id)).where(
                    Principal.kind == "benchmark",
                    Principal.external_subject.like("semantic-recall-burn:%"),
                )
            )
            return int(count or 0)
    finally:
        await engine.dispose()


async def test_real_model_burn_harness_compares_same_suite_and_rolls_back() -> None:
    database_url = os.getenv("BUKMATIKA_DATABASE_URL")
    if database_url is None:
        pytest.skip("PostgreSQL integration URL not configured")

    before = await _benchmark_principal_count(database_url)
    result = await run_semantic_recall_burn(
        settings=Settings(
            database_url=database_url,
            embedding_provider="ollama",
            ollama_embedding_model="benchmark-probe",
        ),
        gateway=_RepresentativeSemanticGateway(),
    )
    after = await _benchmark_principal_count(database_url)

    assert result.provider.provider == "benchmark-probe"
    assert result.provider.model == "representative-semantic-v1"
    assert result.lexical.micro_recall == 11 / 12
    assert result.lexical.macro_recall == 11 / 12
    assert result.lexical.mean_reciprocal_rank == 11 / 12
    assert result.semantic.micro_recall == 1.0
    assert result.semantic.macro_recall == 1.0
    assert result.semantic.mean_reciprocal_rank == 1.0
    assert result.recovered_case_ids == ["paine-natural-paraphrase"]
    assert result.recall_regressed_case_ids == []
    assert result.rank_regressed_case_ids == []
    assert result.paine_recovered is True
    assert result.passed is True
    assert before == after
