from bukmatika.config import Settings


def test_embedding_configuration_is_independent_from_generation_provider() -> None:
    settings = Settings(
        model_provider="none",
        ollama_model=None,
        embedding_provider="ollama",
        ollama_embedding_model="embeddinggemma",
    )

    assert settings.model_provider == "none"
    assert settings.ollama_model is None
    assert settings.embedding_provider == "ollama"
    assert settings.ollama_embedding_model == "embeddinggemma"
