import pytest
from pydantic import ValidationError

from bukmatika.ai.embedding_gateway import EmbeddingRequest
from bukmatika.ai.gateway import ModelDataClassification


def test_embedding_request_rejects_blank_and_oversized_inputs() -> None:
    with pytest.raises(ValidationError):
        EmbeddingRequest(
            inputs=["   "],
            data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
            timeout_seconds=5,
        )

    with pytest.raises(ValidationError):
        EmbeddingRequest(
            inputs=["x" * 12_001],
            data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
            timeout_seconds=5,
        )
