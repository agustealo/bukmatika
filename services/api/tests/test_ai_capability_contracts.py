from uuid import UUID

import pytest

from bukmatika.ai.capability_contracts import (
    CapabilityArgumentContractRegistry,
    CapabilityArgumentsInvalid,
)
from bukmatika.ai.domain import CapabilityName
from bukmatika.personalization.domain import ContextLibraryEntry, ContextManifest, ContextTask

ENTRY_ID = UUID(int=201)


def _context() -> ContextManifest:
    return ContextManifest(
        task=ContextTask.RESEARCH,
        ai_enabled=True,
        learning_enabled=True,
        autonomy_level=2,
        model_context_ready=True,
        preferences=[],
        goal=None,
        library_entries=[
            ContextLibraryEntry(
                library_entry_id=ENTRY_ID,
                work_id=UUID(int=202),
                edition_id=UUID(int=203),
                title="Contract test book",
                document_ids=[UUID(int=204)],
                inclusion_reason="Explicitly selected for research.",
            )
        ],
        available_capabilities=[CapabilityName.RESEARCH_SEARCH.value],
        exclusion_reasons=[],
    )


def test_research_search_contract_returns_typed_normalized_request() -> None:
    request = CapabilityArgumentContractRegistry().validate(
        capability=CapabilityName.RESEARCH_SEARCH,
        arguments={
            "query": "  maritime   navigation  ",
            "library_entry_ids": [str(ENTRY_ID)],
            "limit": 8,
        },
        context=_context(),
    )

    assert request.model_dump(mode="json") == {
        "query": "maritime navigation",
        "library_entry_ids": [str(ENTRY_ID)],
        "limit": 8,
    }


def test_research_search_contract_rejects_context_widening() -> None:
    with pytest.raises(CapabilityArgumentsInvalid, match="persisted context"):
        CapabilityArgumentContractRegistry().validate(
            capability=CapabilityName.RESEARCH_SEARCH,
            arguments={
                "query": "navigation",
                "library_entry_ids": [str(UUID(int=999))],
                "limit": 8,
            },
            context=_context(),
        )
