from uuid import uuid4

from bukmatika.research.domain import GroundedResearchRequest, ResearchSearchRequest
from bukmatika.research.grounded import _retrieval_query


def test_long_grounded_question_becomes_valid_bounded_lexical_query() -> None:
    long_question = "maritime trade " + " ".join(
        f"contextterm{index}" for index in range(80)
    )
    request = GroundedResearchRequest(
        question=long_question,
        library_entry_ids=[uuid4()],
    )

    assert len(request.question) > 200
    retrieval_query = _retrieval_query(request.question)
    assert len(retrieval_query) <= 200
    assert retrieval_query.startswith("maritime OR trade")

    validated = ResearchSearchRequest(
        query=retrieval_query,
        library_entry_ids=request.library_entry_ids,
        limit=request.max_passages,
    )
    assert validated.query == retrieval_query
