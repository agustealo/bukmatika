from pathlib import Path


def test_semantic_slice_has_no_vector_store_dependency() -> None:
    project = Path(__file__).resolve().parents[1]
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8").casefold()

    assert "pgvector" not in pyproject
    assert "pinecone" not in pyproject
    assert "milvus" not in pyproject
    assert "qdrant" not in pyproject
