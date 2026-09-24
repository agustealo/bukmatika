from bukmatika.config import Settings


def test_semantic_candidate_budget_has_bounded_defaults() -> None:
    settings = Settings()

    assert settings.semantic_search_max_chunks == 256
    assert settings.semantic_search_batch_size == 24
