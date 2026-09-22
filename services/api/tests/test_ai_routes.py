from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from bukmatika.ai.routes import grounded_research_answer
from bukmatika.identity import AuthenticatedPrincipal
from bukmatika.persistence.personalization import ContextSelectionDenied
from bukmatika.research import ReaderResearchContextRequest, ResearchEvidenceBundleRequest


class _ContextDeniedService:
    async def answer(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise ContextSelectionDenied("Selected library entry is unavailable")


async def test_grounded_answer_maps_context_ownership_denial_to_404() -> None:
    entry_id = uuid4()
    identity = AuthenticatedPrincipal(
        principal_id=uuid4(),
        session_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    request = ResearchEvidenceBundleRequest(
        question="What does this passage establish?",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry_id,
            document_id=uuid4(),
            section_id=uuid4(),
            char_offset=0,
        ),
        library_entry_ids=[entry_id],
        related_limit=0,
    )

    with pytest.raises(HTTPException) as captured:
        await grounded_research_answer(
            request=request,
            identity=identity,
            service=_ContextDeniedService(),  # type: ignore[arg-type]
        )

    assert captured.value.status_code == 404
    assert captured.value.detail == {"code": "RESEARCH_SELECTION_UNAVAILABLE"}
