from inspect import signature

from bukmatika.research.service import ResearchService


def test_ordinary_search_signature_has_no_semantic_toggle() -> None:
    parameters = signature(ResearchService.search).parameters

    assert "semantic" not in parameters
    assert "embedding" not in parameters
