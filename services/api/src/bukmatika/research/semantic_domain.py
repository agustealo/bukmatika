from uuid import UUID

from pydantic import BaseModel

from bukmatika.research.domain import ResearchPassageResponse, ResearchSearchRequest


class ResearchSemanticSearchRequest(ResearchSearchRequest):
    """Explicit semantic retrieval request over selected owned books."""


class ResearchSemanticSearchResponse(BaseModel):
    query: str
    selected_library_entry_ids: list[UUID]
    passages: list[ResearchPassageResponse]
    candidate_count: int
    provider: str
    model: str
    routing: str
