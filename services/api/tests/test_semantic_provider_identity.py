from bukmatika.ai.gateway import ModelProviderIdentity


def test_semantic_provider_identity_uses_existing_canonical_shape() -> None:
    identity = ModelProviderIdentity(
        provider="ollama",
        model="embeddinggemma",
        routing="local",
    )

    assert identity.model_dump() == {
        "provider": "ollama",
        "model": "embeddinggemma",
        "routing": "local",
    }
