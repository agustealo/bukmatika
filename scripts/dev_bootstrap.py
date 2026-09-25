from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
ENV_EXAMPLE: Final = REPO_ROOT / ".env.example"
ENV_FILE: Final = REPO_ROOT / ".env"
VENV_DIR: Final = REPO_ROOT / ".venv"
API_PROJECT: Final = REPO_ROOT / "services" / "api"
ALEMBIC_CONFIG: Final = API_PROJECT / "alembic.ini"
MIN_PYTHON: Final = (3, 12)
MAX_PYTHON_EXCLUSIVE: Final = (3, 15)
MIN_NODE_MAJOR: Final = 24
DATABASE_WAIT_SECONDS: Final = 60.0
DATABASE_POLL_SECONDS: Final = 1.0
LOCAL_DATABASE_URL: Final = (
    "postgresql+psycopg://bukmatika:bukmatika@localhost:5432/bukmatika"
)


class BootstrapError(RuntimeError):
    pass


def _run(
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
    check: bool = True,
    capture_output: bool = False,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    printable = " ".join(command)
    print(f"+ {printable}")
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture_output,
        env=env,
    )


def _require_executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise BootstrapError(f"Required executable not found on PATH: {name}")
    return path


def _validate_python() -> None:
    version = sys.version_info[:2]
    if version < MIN_PYTHON or version >= MAX_PYTHON_EXCLUSIVE:
        raise BootstrapError(
            "Bukmatika requires Python >=3.12,<3.15; "
            f"current interpreter is {sys.version_info.major}.{sys.version_info.minor}."
        )


def _parse_node_major(value: str) -> int:
    match = re.fullmatch(r"v?(\d+)(?:\.\d+){0,2}", value.strip())
    if match is None:
        raise BootstrapError(f"Could not parse Node.js version: {value!r}")
    return int(match.group(1))


def _validate_node(node: str) -> None:
    result = _run([node, "--version"], capture_output=True)
    major = _parse_node_major(result.stdout)
    if major < MIN_NODE_MAJOR:
        raise BootstrapError(
            f"Bukmatika requires Node.js >=24; detected Node.js {result.stdout.strip()}."
        )


def _validate_docker_compose(docker: str) -> None:
    _run([docker, "compose", "version"], capture_output=True)
    _run([docker, "compose", "-f", "compose.yaml", "config", "--quiet"])


def _venv_python(*, os_name: str | None = None) -> Path:
    resolved_os = os_name or os.name
    if resolved_os == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _ensure_env_file() -> bool:
    if ENV_FILE.exists():
        return False
    if not ENV_EXAMPLE.is_file():
        raise BootstrapError(f"Missing environment template: {ENV_EXAMPLE}")
    shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    return True


def _ensure_virtualenv() -> Path:
    python = _venv_python()
    if not python.is_file():
        _run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if not python.is_file():
        raise BootstrapError(f"Virtual environment did not create {python}")
    return python


def _install_dependencies(venv_python: Path, npm: str) -> None:
    _run(
        [str(venv_python), "-m", "pip", "install", "-e", f"{API_PROJECT}[dev]"],
    )
    _run([npm, "install", "--no-audit", "--no-fund"])


def _start_database(docker: str) -> None:
    _run([docker, "compose", "-f", "compose.yaml", "up", "-d", "postgres"])


def _database_ready(docker: str) -> bool:
    result = _run(
        [
            docker,
            "compose",
            "-f",
            "compose.yaml",
            "exec",
            "-T",
            "postgres",
            "pg_isready",
            "-U",
            "bukmatika",
            "-d",
            "bukmatika",
        ],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _wait_for_database(docker: str) -> None:
    deadline = time.monotonic() + DATABASE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _database_ready(docker):
            return
        time.sleep(DATABASE_POLL_SECONDS)
    raise BootstrapError(
        "PostgreSQL did not become ready within 60 seconds. "
        "Inspect it with: docker compose logs postgres"
    )


def _local_database_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["BUKMATIKA_DATABASE_URL"] = LOCAL_DATABASE_URL
    return environment


def _migrate(venv_python: Path) -> None:
    _run(
        [
            str(venv_python),
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            "upgrade",
            "head",
        ],
        env=_local_database_environment(),
    )


def _print_ready(venv_python: Path, *, env_created: bool) -> None:
    relative_python = venv_python.relative_to(REPO_ROOT)
    print("\nBukmatika local development is ready.")
    if env_created:
        print("Created .env from .env.example without overwriting existing local settings.")
    else:
        print("Kept the existing .env unchanged.")
    print(f"Bootstrap migrations were fenced to {LOCAL_DATABASE_URL}.")
    print("\nStart the API in terminal 1:")
    print(
        f"  {relative_python} -m uvicorn bukmatika.main:app "
        "--reload --host 127.0.0.1 --port 8000"
    )
    print("\nStart the web app in terminal 2:")
    print("  npm run web:dev")
    print("\nStop PostgreSQL when finished:")
    print("  docker compose down")
    print("\nLocal AI remains optional and is not installed or started by this bootstrap.")


def _preflight() -> tuple[str, str]:
    _validate_python()
    node = _require_executable("node")
    npm = _require_executable("npm")
    docker = _require_executable("docker")
    _validate_node(node)
    _validate_docker_compose(docker)
    return npm, docker


def bootstrap(*, check_only: bool = False) -> None:
    npm, docker = _preflight()
    if check_only:
        print("Bukmatika development prerequisites and compose configuration are valid.")
        return

    env_created = _ensure_env_file()
    venv_python = _ensure_virtualenv()
    _install_dependencies(venv_python, npm)
    _start_database(docker)
    _wait_for_database(docker)
    _migrate(venv_python)
    _print_ready(venv_python, env_created=env_created)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the canonical Bukmatika local development environment."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate prerequisites and Compose configuration without changing local state.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        bootstrap(check_only=args.check)
    except (BootstrapError, subprocess.CalledProcessError, OSError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
