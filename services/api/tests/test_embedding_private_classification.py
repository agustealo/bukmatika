from bukmatika.ai.embedding_gateway import EmbeddingRequest
from bukmatika.ai.gateway import ModelDataClassification


def test_semantic_embedding_request_marks_book_text_private() -> None:
    request = EmbeddingRequest(
        inputs=["private canonical book text"],
        data_classification=ModelDataClassification.PRIVATE_USER_CONTEXT,
        timeout_seconds=5,
    )

    assert request.data_classification is ModelDataClassification.PRIVATE_USER_CONTEXT
