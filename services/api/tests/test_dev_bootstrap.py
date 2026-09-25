from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP_PATH = REPO_ROOT / "scripts" / "dev_bootstrap.py"


def _load_bootstrap() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bukmatika_dev_bootstrap", BOOTSTRAP_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load development bootstrap module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = _load_bootstrap()


def test_parse_node_major_accepts_supported_version_shapes() -> None:
    assert bootstrap._parse_node_major("v24.9.0") == 24
    assert bootstrap._parse_node_major("24.1") == 24
    assert bootstrap._parse_node_major("25") == 25


def test_parse_node_major_rejects_untrusted_output() -> None:
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._parse_node_major("node version 24.9.0")


def test_venv_python_path_is_platform_specific() -> None:
    assert bootstrap._venv_python(os_name="posix") == REPO_ROOT / ".venv" / "bin" / "python"
    assert bootstrap._venv_python(os_name="nt") == (
        REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    )


def test_ensure_env_file_never_overwrites_existing_local_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    example.write_text("SAFE=template\n", encoding="utf-8")
    target.write_text("SAFE=local\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "ENV_EXAMPLE", example)
    monkeypatch.setattr(bootstrap, "ENV_FILE", target)

    assert bootstrap._ensure_env_file() is False
    assert target.read_text(encoding="utf-8") == "SAFE=local\n"


def test_ensure_env_file_copies_template_only_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = tmp_path / ".env.example"
    target = tmp_path / ".env"
    example.write_text("SAFE=template\n", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "ENV_EXAMPLE", example)
    monkeypatch.setattr(bootstrap, "ENV_FILE", target)

    assert bootstrap._ensure_env_file() is True
    assert target.read_text(encoding="utf-8") == "SAFE=template\n"


def test_ensure_env_file_fails_if_template_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bootstrap, "ENV_EXAMPLE", tmp_path / ".env.example")
    monkeypatch.setattr(bootstrap, "ENV_FILE", tmp_path / ".env")

    with pytest.raises(bootstrap.BootstrapError):
        bootstrap._ensure_env_file()


def test_local_database_environment_overrides_external_database_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BUKMATIKA_DATABASE_URL", "postgresql://remote.invalid/production")
    environment = bootstrap._local_database_environment()

    assert environment["BUKMATIKA_DATABASE_URL"] == bootstrap.LOCAL_DATABASE_URL
    assert os.environ["BUKMATIKA_DATABASE_URL"] == "postgresql://remote.invalid/production"
