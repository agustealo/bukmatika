from pathlib import Path


def test_semantic_service_does_not_persist_vectors() -> None:
    service = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "bukmatika"
        / "research"
        / "semantic.py"
    ).read_text(encoding="utf-8").casefold()

    assert "insert(" not in service
    assert "update(" not in service
    assert "vector" not in service or "query_vector" in service
