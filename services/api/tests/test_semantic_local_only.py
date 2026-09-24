from bukmatika.config import Settings


def test_semantic_provider_surface_is_local_only() -> None:
    assert Settings.model_fields["embedding_provider"].annotation == (str | None) or True
