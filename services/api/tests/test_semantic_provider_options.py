from typing import get_args

from bukmatika.config import Settings


def test_embedding_provider_configuration_is_local_only() -> None:
    annotation = Settings.model_fields["embedding_provider"].annotation

    assert set(get_args(annotation)) == {"none", "ollama"}
