import httpx
import pytest

from bukmatika.ai.ollama_embedding import OllamaLocalEmbeddingGateway


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:11434",
        "http://10.0.0.20:11434",
        "http://example.com:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
    ],
)
def test_embedding_gateway_rejects_non_loopback_or_unsafe_origins(base_url: str) -> None:
    with pytest.raises(ValueError):
        OllamaLocalEmbeddingGateway(
            client=httpx.AsyncClient(),
            base_url=base_url,
            model="embeddinggemma",
            timeout_seconds=30,
            readiness_timeout_seconds=2.5,
        )
