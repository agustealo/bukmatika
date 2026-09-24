from pathlib import Path


def test_semantic_slice_introduces_no_vector_or_embedding_persistence() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "bukmatika"
    forbidden = {
        "embedding_models.py",
        "embeddings.py",
        "vector_store.py",
        "vector_models.py",
    }
    existing = {path.name for path in root.rglob("*.py")}

    assert existing.isdisjoint(forbidden)
