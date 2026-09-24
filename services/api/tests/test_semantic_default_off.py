from bukmatika.config import Settings


def test_semantic_retrieval_is_fail_closed_by_default() -> None:
    settings = Settings()

    assert settings.embedding_provider == "none"
    assert settings.ollama_embedding_model is None
