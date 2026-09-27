from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_PATH = REPO_ROOT / "scripts" / "deploy.py"


def _load_deploy() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bukmatika_deploy", DEPLOY_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("Could not load deployment module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy()


def _valid_environment() -> dict[str, str]:
    return {
        "BUKMATIKA_BIND_ADDRESS": "127.0.0.1",
        "BUKMATIKA_HTTP_PORT": "8080",
        "BUKMATIKA_PUBLIC_ORIGIN": "http://127.0.0.1:8080",
        "BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE": "false",
        "BUKMATIKA_POSTGRES_PASSWORD": "A" * 40,
        "BUKMATIKA_MODEL_PROVIDER": "none",
        "BUKMATIKA_EMBEDDING_PROVIDER": "none",
    }


def test_render_environment_replaces_exact_password_marker() -> None:
    rendered = deploy._render_environment(
        "SAFE=1\nBUKMATIKA_POSTGRES_PASSWORD=GENERATE_ME\n",
        "A" * 40,
    )
    assert "GENERATE_ME" not in rendered
    assert f"BUKMATIKA_POSTGRES_PASSWORD={'A' * 40}" in rendered


def test_parse_env_rejects_duplicate_keys() -> None:
    with pytest.raises(deploy.DeploymentError, match="Duplicate environment key"):
        deploy._parse_env("SAFE=one\nSAFE=two\n")


def test_local_http_deployment_is_valid() -> None:
    deploy._validate_environment(_valid_environment())


def test_remote_http_origin_is_rejected() -> None:
    values = _valid_environment()
    values["BUKMATIKA_PUBLIC_ORIGIN"] = "http://books.example.com"
    with pytest.raises(deploy.DeploymentError, match="must use https"):
        deploy._validate_environment(values)


def test_https_requires_secure_session_cookie() -> None:
    values = _valid_environment()
    values["BUKMATIKA_PUBLIC_ORIGIN"] = "https://books.example.com"
    with pytest.raises(deploy.DeploymentError, match=r"require.*SECURE_COOKIE=true"):
        deploy._validate_environment(values)

    values["BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE"] = "true"
    deploy._validate_environment(values)


def test_production_ingress_cannot_bind_publicly() -> None:
    values = _valid_environment()
    values["BUKMATIKA_BIND_ADDRESS"] = "0.0.0.0"
    with pytest.raises(deploy.DeploymentError, match=r"must bind to 127\.0\.0\.1"):
        deploy._validate_environment(values)


def test_placeholder_or_weak_database_password_is_rejected() -> None:
    values = _valid_environment()
    values["BUKMATIKA_POSTGRES_PASSWORD"] = deploy.PASSWORD_PLACEHOLDER
    with pytest.raises(deploy.DeploymentError, match="generated URL-safe secret"):
        deploy._validate_environment(values)

    values["BUKMATIKA_POSTGRES_PASSWORD"] = "too-short"
    with pytest.raises(deploy.DeploymentError, match="generated URL-safe secret"):
        deploy._validate_environment(values)


def test_container_deployment_cannot_enable_ollama_transport() -> None:
    values = _valid_environment()
    values["BUKMATIKA_MODEL_PROVIDER"] = "ollama"
    with pytest.raises(deploy.DeploymentError, match="loopback-only Ollama"):
        deploy._validate_environment(values)

    values = _valid_environment()
    values["BUKMATIKA_EMBEDDING_PROVIDER"] = "ollama"
    with pytest.raises(deploy.DeploymentError, match="loopback-only Ollama"):
        deploy._validate_environment(values)


def test_ensure_env_file_never_overwrites_existing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = tmp_path / ".env.production.example"
    target = tmp_path / ".env.production"
    template.write_text("BUKMATIKA_POSTGRES_PASSWORD=GENERATE_ME\n", encoding="utf-8")
    target.write_text("BUKMATIKA_POSTGRES_PASSWORD=existing-secret\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_TEMPLATE", template)
    monkeypatch.setattr(deploy, "ENV_FILE", target)

    assert deploy._ensure_env_file() is False
    assert target.read_text(encoding="utf-8") == "BUKMATIKA_POSTGRES_PASSWORD=existing-secret\n"


def test_ensure_env_file_generates_secret_when_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = tmp_path / ".env.production.example"
    target = tmp_path / ".env.production"
    template.write_text("BUKMATIKA_POSTGRES_PASSWORD=GENERATE_ME\n", encoding="utf-8")
    monkeypatch.setattr(deploy, "ENV_TEMPLATE", template)
    monkeypatch.setattr(deploy, "ENV_FILE", target)

    assert deploy._ensure_env_file() is True
    values = deploy._parse_env(target.read_text(encoding="utf-8"))
    assert deploy.PASSWORD_PATTERN.fullmatch(values["BUKMATIKA_POSTGRES_PASSWORD"])
