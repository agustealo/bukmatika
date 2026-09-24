from pathlib import Path


def test_embedding_runtime_has_no_model_pull_path() -> None:
    ai_root = Path(__file__).resolve().parents[1] / "src" / "bukmatika" / "ai"
    source = "\n".join(path.read_text(encoding="utf-8") for path in ai_root.glob("*.py"))

    assert "/api/pull" not in source
