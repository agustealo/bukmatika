from pathlib import Path


def test_semantic_slice_introduces_no_embedding_cache() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "bukmatika"
    names = {path.name.casefold() for path in root.rglob("*.py")}

    assert "embedding_cache.py" not in names
    assert "semantic_cache.py" not in names
