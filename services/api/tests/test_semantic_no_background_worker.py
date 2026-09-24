from pathlib import Path


def test_semantic_slice_introduces_no_background_embedding_worker() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "bukmatika"
    names = {path.name.casefold() for path in root.rglob("*.py")}

    assert "embedding_worker.py" not in names
    assert "semantic_worker.py" not in names
