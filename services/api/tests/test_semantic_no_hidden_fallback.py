from pathlib import Path


def test_ordinary_research_service_does_not_import_embedding_runtime() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "bukmatika"
        / "research"
        / "service.py"
    ).read_text(encoding="utf-8")

    assert "EmbeddingGateway" not in source
    assert "semantic" not in source.casefold()
