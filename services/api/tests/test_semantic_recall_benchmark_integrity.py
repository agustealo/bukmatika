import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import bukmatika.research.semantic_recall_burn as semantic_recall_burn
from bukmatika.config import Settings
from bukmatika.persistence.models import Principal
from bukmatika.research.recall_eval import ResearchRecallSuiteInvalid
from bukmatika.research.representative_benchmark import (
    seed_representative_public_domain_benchmark,
)
from bukmatika.research.semantic_domain import (
    ResearchSemanticSearchRequest,
    ResearchSemanticSearchResponse,
)


class _CutoffProbeService:
    def __init__(self) -> None:
        self.limits: list[int] = []

    async def search(
        self,
        *,
        principal_id: UUID,
        request: ResearchSemanticSearchRequest,
    ) -> ResearchSemanticSearchResponse:
        del principal_id
        self.limits.append(request.limit)
        return ResearchSemanticSearchResponse(
            query=request.query,
            selected_library_entry_ids=request.library_entry_ids,
            passages=[],
            candidate_count=request.limit,
            provider="probe",
            model="cutoff-probe",
            routing="local",
        )


async def test_semantic_benchmark_rejects_cutoff_that_contains_entire_candidate_set(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = Principal(kind="benchmark", external_subject="semantic-cutoff-integrity")
    session.add(principal)
    await session.flush()
    suite = await seed_representative_public_domain_benchmark(
        session,
        principal=principal,
        scratch_dir=tmp_path,
    )
    service = _CutoffProbeService()
    evaluator = semantic_recall_burn.SemanticRecallEvaluator(
        service=service,  # type: ignore[arg-type]
        session=session,
    )

    with pytest.raises(ResearchRecallSuiteInvalid, match="cutoff must be smaller"):
        await evaluator.evaluate(suite)

    assert service.limits == [5]


def test_semantic_burn_invalid_settings_exit_as_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def invalid_settings() -> Settings:
        return Settings(embedding_timeout_seconds=0)

    monkeypatch.setattr(semantic_recall_burn, "get_settings", invalid_settings)

    with pytest.raises(SystemExit) as captured:
        semantic_recall_burn.main()

    assert captured.value.code == 2
    payload = json.loads(capsys.readouterr().err)
    assert payload["status"] == "unavailable"
    assert payload["code"] == "SETTINGS_INVALID"
