from bukmatika.persistence.events import SemanticEventType


def test_semantic_search_event_type_is_stable() -> None:
    assert (
        SemanticEventType.RESEARCH_SEMANTIC_SEARCH_COMPLETED.value
        == "research.semantic_search_completed"
    )
