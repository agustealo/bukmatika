from __future__ import annotations

import argparse
import ipaddress
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
COMPOSE_FILE: Final = REPO_ROOT / "compose.production.yaml"
ENV_TEMPLATE: Final = REPO_ROOT / ".env.production.example"
ENV_FILE: Final = REPO_ROOT / ".env.production"
PASSWORD_PLACEHOLDER: Final = "GENERATE_ME"
PASSWORD_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{32,128}")
READY_TIMEOUT_SECONDS: Final = 180.0
READY_POLL_SECONDS: Final = 2.0


class DeploymentError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command))
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def _require_docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise DeploymentError("Docker with the Compose plugin is required")
    _run([docker, "compose", "version"], capture_output=True)
    return docker


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise DeploymentError(f"Invalid environment line {line_number}: expected KEY=value")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise DeploymentError(f"Invalid environment key on line {line_number}: {key!r}")
        if key in values:
            raise DeploymentError(f"Duplicate environment key: {key}")
        values[key] = value.strip()
    return values


def _render_environment(template: str, password: str) -> str:
    marker = f"BUKMATIKA_POSTGRES_PASSWORD={PASSWORD_PLACEHOLDER}"
    if template.count(marker) != 1:
        raise DeploymentError("Production environment template has an invalid password marker")
    return template.replace(marker, f"BUKMATIKA_POSTGRES_PASSWORD={password}")


def _ensure_env_file() -> bool:
    if ENV_FILE.exists():
        return False
    if not ENV_TEMPLATE.is_file():
        raise DeploymentError(f"Missing production environment template: {ENV_TEMPLATE}")
    password = secrets.token_urlsafe(32)
    if PASSWORD_PATTERN.fullmatch(password) is None:
        raise DeploymentError("Generated database password did not satisfy the deployment contract")
    rendered = _render_environment(ENV_TEMPLATE.read_text(encoding="utf-8"), password)
    ENV_FILE.write_text(rendered, encoding="utf-8")
    if os.name != "nt":
        ENV_FILE.chmod(0o600)
    return True


def _is_loopback_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    normalized = hostname.rstrip(".").casefold()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_public_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise DeploymentError("BUKMATIKA_PUBLIC_ORIGIN must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise DeploymentError("BUKMATIKA_PUBLIC_ORIGIN cannot contain credentials")
    if not parsed.hostname:
        raise DeploymentError("BUKMATIKA_PUBLIC_ORIGIN must include a hostname")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise DeploymentError("BUKMATIKA_PUBLIC_ORIGIN must be an origin without path/query/fragment")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise DeploymentError("Remote Bukmatika origins must use https")
    return value.rstrip("/")


def _boolean(values: dict[str, str], key: str) -> bool:
    value = values.get(key, "").casefold()
    if value == "true":
        return True
    if value == "false":
        return False
    raise DeploymentError(f"{key} must be true or false")


def _validate_environment(values: dict[str, str]) -> None:
    required = {
        "BUKMATIKA_BIND_ADDRESS",
        "BUKMATIKA_HTTP_PORT",
        "BUKMATIKA_PUBLIC_ORIGIN",
        "BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE",
        "BUKMATIKA_POSTGRES_PASSWORD",
        "BUKMATIKA_MODEL_PROVIDER",
        "BUKMATIKA_EMBEDDING_PROVIDER",
    }
    missing = sorted(key for key in required if key not in values)
    if missing:
        raise DeploymentError(f"Missing production environment keys: {', '.join(missing)}")

    if values["BUKMATIKA_BIND_ADDRESS"] != "127.0.0.1":
        raise DeploymentError(
            "Production ingress must bind to 127.0.0.1; put a TLS reverse proxy in front for remote access"
        )
    try:
        port = int(values["BUKMATIKA_HTTP_PORT"])
    except ValueError as exc:
        raise DeploymentError("BUKMATIKA_HTTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise DeploymentError("BUKMATIKA_HTTP_PORT must be between 1 and 65535")

    origin = _validate_public_origin(values["BUKMATIKA_PUBLIC_ORIGIN"])
    secure_cookie = _boolean(values, "BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE")
    if origin.startswith("https://") and not secure_cookie:
        raise DeploymentError("HTTPS deployments require BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE=true")
    if origin.startswith("http://") and secure_cookie:
        raise DeploymentError("Loopback HTTP deployments require BUKMATIKA_LOCAL_SESSION_SECURE_COOKIE=false")

    password = values["BUKMATIKA_POSTGRES_PASSWORD"]
    if password == PASSWORD_PLACEHOLDER or PASSWORD_PATTERN.fullmatch(password) is None:
        raise DeploymentError(
            "BUKMATIKA_POSTGRES_PASSWORD must be a generated URL-safe secret of 32-128 characters"
        )

    if values["BUKMATIKA_MODEL_PROVIDER"] != "none":
        raise DeploymentError(
            "Container deployment keeps model generation disabled to preserve loopback-only Ollama routing"
        )
    if values["BUKMATIKA_EMBEDDING_PROVIDER"] != "none":
        raise DeploymentError(
            "Container deployment keeps semantic embeddings disabled to preserve loopback-only Ollama routing"
        )


def _environment() -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise DeploymentError("Missing .env.production; run `python scripts/deploy.py init` first")
    values = _parse_env(ENV_FILE.read_text(encoding="utf-8"))
    _validate_environment(values)
    return values


def _compose_command(docker: str, env_file: Path = ENV_FILE) -> list[str]:
    return [
        docker,
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(COMPOSE_FILE),
    ]


def _validate_compose(docker: str) -> None:
    _run([*_compose_command(docker), "config", "--quiet"])


def _local_ingress(values: dict[str, str]) -> str:
    return f"http://127.0.0.1:{int(values['BUKMATIKA_HTTP_PORT'])}"


def _url_ready(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=5) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _wait_for_stack(values: dict[str, str]) -> None:
    base = _local_ingress(values)
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _url_ready(f"{base}/ready") and _url_ready(f"{base}/"):
            return
        time.sleep(READY_POLL_SECONDS)
    raise DeploymentError(
        "Production stack did not become ready within 180 seconds; "
        "inspect it with `python scripts/deploy.py status` and `python scripts/deploy.py logs`"
    )


def initialize() -> None:
    created = _ensure_env_file()
    if created:
        print("Created .env.production with a generated database password (mode 0600 where supported).")
    else:
        print("Kept existing .env.production unchanged.")
    _environment()


def check() -> None:
    docker = _require_docker()
    _environment()
    if not COMPOSE_FILE.is_file():
        raise DeploymentError(f"Missing production Compose file: {COMPOSE_FILE}")
    _validate_compose(docker)
    print("Bukmatika production deployment configuration is valid.")


def up() -> None:
    _ensure_env_file()
    values = _environment()
    docker = _require_docker()
    _validate_compose(docker)
    _run([*_compose_command(docker), "up", "-d", "--build"])
    _wait_for_stack(values)
    print(f"Bukmatika is ready behind the local ingress at {_local_ingress(values)}.")
    print(f"Configured browser origin: {values['BUKMATIKA_PUBLIC_ORIGIN']}")
    if values["BUKMATIKA_PUBLIC_ORIGIN"].startswith("https://"):
        print("TLS is expected to terminate in the external reverse proxy in front of this loopback ingress.")
    print("Database and book storage use named volumes and are preserved by `deploy.py down`.")


def _env_for_management() -> Path:
    return ENV_FILE if ENV_FILE.is_file() else ENV_TEMPLATE


def down() -> None:
    docker = _require_docker()
    _run([*_compose_command(docker, _env_for_management()), "down"])


def status() -> None:
    docker = _require_docker()
    _run([*_compose_command(docker, _env_for_management()), "ps"])


def logs(*, follow: bool, tail: int) -> None:
    docker = _require_docker()
    command = [*_compose_command(docker, _env_for_management()), "logs", "--tail", str(tail)]
    if follow:
        command.append("--follow")
    _run(command)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the canonical Bukmatika production stack.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create .env.production once with a strong database secret.")
    subparsers.add_parser("check", help="Validate Docker Compose and production settings.")
    subparsers.add_parser("up", help="Build, migrate, start, and verify the production stack.")
    subparsers.add_parser("down", help="Stop containers without deleting persistent volumes.")
    subparsers.add_parser("status", help="Show production container state.")
    logs_parser = subparsers.add_parser("logs", help="Show bounded Docker service logs.")
    logs_parser.add_argument("--follow", action="store_true")
    logs_parser.add_argument("--tail", type=int, default=200)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "init":
            initialize()
        elif args.command == "check":
            check()
        elif args.command == "up":
            up()
        elif args.command == "down":
            down()
        elif args.command == "status":
            status()
        elif args.command == "logs":
            if args.tail < 1 or args.tail > 10_000:
                raise DeploymentError("--tail must be between 1 and 10000")
            logs(follow=args.follow, tail=args.tail)
        else:
            raise DeploymentError(f"Unsupported command: {args.command}")
    except (DeploymentError, subprocess.CalledProcessError, OSError) as exc:
        print(f"deployment failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
