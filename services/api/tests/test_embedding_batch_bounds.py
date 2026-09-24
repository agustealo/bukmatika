import pytest
from pydantic import ValidationError

from bukmatika.ai.embedding_gateway import EmbeddingBatch
from bukmatika.ai.gateway import ModelProviderIdentity


def test_embedding_batch_rejects_inconsistent_dimensions() -> None:
    with pytest.raises(ValidationError):
        EmbeddingBatch(
            identity=ModelProviderIdentity(
                provider="probe",
                model="embedding-probe",
                routing="local",
            ),
            vectors=[[1.0, 0.0], [1.0]],
        )
