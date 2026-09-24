from pathlib import Path


def test_semantic_slice_introduces_no_graph_runtime() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "bukmatika"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))

    assert "neo4j" not in source.casefold()
