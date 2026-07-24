from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "preflight.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


@pytest.fixture
def clone_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "clone"
    root.mkdir()
    (root / "frontend").mkdir()
    (root / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://jobagent:jobagent@localhost/jobagent\n"
        "GOOGLE_API_KEY=value-that-must-not-be-printed\n"
        "LANGSMITH_API_KEY=\n"
    )
    (root / "uv.lock").write_text('requires-python = ">=3.12"\n')
    (root / "frontend" / "package-lock.json").write_text("{}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(
        bin_dir / "docker",
        """case "$*" in
  "--version") echo "Docker version 27.0.0" ;;
  "context show") echo "default" ;;
  "context inspect --format {{.Endpoints.docker.Host}} default")
    echo "unix:///var/run/docker.sock"
    ;;
  "info") exit 0 ;;
  "compose version") echo "Docker Compose version v2.30.0" ;;
  "compose ps --status running --services db") echo "db" ;;
  "compose exec -T db pg_isready -U jobagent -d jobagent") exit 0 ;;
  *) echo "unexpected docker arguments: $*" >&2; exit 9 ;;
esac""",
    )
    _executable(bin_dir / "uv", 'test "$1" = "--version" && echo "uv 0.9.0"')
    # A broken system Python must be irrelevant: uv provisions the project interpreter.
    _executable(bin_dir / "python3", "exit 99")
    _executable(bin_dir / "node", 'test "$1" = "-p" && echo "22.12.0"')
    _executable(bin_dir / "npm", 'test "$1" = "--version" && echo "10.9.0"')

    env = os.environ.copy()
    env.pop("DOCKER_HOST", None)
    env.pop("DOCKER_CONTEXT", None)
    env.update(
        {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "PREFLIGHT_ROOT": str(root),
            "PREFLIGHT_DB_RETRY_DELAY": "0",
            "PREFLIGHT_DB_RETRY_ATTEMPTS": "3",
        }
    )
    return root, env


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_preflight_passes_with_uv_owned_python_and_local_db(
    clone_environment: tuple[Path, dict[str, str]],
) -> None:
    _, env = clone_environment

    result = _run(env=env)

    assert result.returncode == 0
    assert "Preflight passed." in result.stdout
    assert "local Postgres accepts connections" in result.stdout
    assert "will provision the locked Python interpreter" in result.stdout
    assert "uv sync --locked --dev" in result.stdout
    assert "npm ci" in result.stdout
    assert "value-that-must-not-be-printed" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("DATABASE_URL", ""),
        ("GOOGLE_API_KEY", ""),
        ("GOOGLE_API_KEY", "your-google-ai-studio-key-here"),
        ("GOOGLE_API_KEY", "changeme"),
    ],
)
def test_preflight_rejects_missing_empty_or_placeholder_required_env_keys(
    clone_environment: tuple[Path, dict[str, str]],
    key: str,
    value: str,
) -> None:
    root, env = clone_environment
    values = {
        "DATABASE_URL": "postgresql+asyncpg://jobagent:jobagent@localhost/jobagent",
        "GOOGLE_API_KEY": "configured-local-key",
    }
    values[key] = value
    (root / ".env").write_text("".join(f"{name}={item}\n" for name, item in values.items()))

    result = _run("--skip-db", env=env)

    assert result.returncode == 1
    assert f".env key {key} is missing, empty, or still a placeholder" in result.stderr
    if value:
        assert value not in result.stdout + result.stderr


def test_preflight_does_not_require_optional_empty_secrets(
    clone_environment: tuple[Path, dict[str, str]],
) -> None:
    _, env = clone_environment

    result = _run("--skip-db", env=env)

    assert result.returncode == 0
    assert "LANGSMITH_API_KEY" not in result.stdout + result.stderr


def test_preflight_reports_missing_env(
    clone_environment: tuple[Path, dict[str, str]],
) -> None:
    root, env = clone_environment
    (root / ".env").unlink()

    result = _run("--skip-db", env=env)

    assert result.returncode == 1
    assert ".env is missing" in result.stderr
    assert "SKIP local db" in result.stdout


def test_preflight_retries_database_from_starting_to_ready(
    clone_environment: tuple[Path, dict[str, str]],
    tmp_path: Path,
) -> None:
    _, env = clone_environment
    count_file = tmp_path / "pg-attempts"
    docker = Path(env["PATH"].split(":", maxsplit=1)[0]) / "docker"
    _executable(
        docker,
        f"""case "$*" in
  "--version"|"info"|"compose version") exit 0 ;;
  "context show") echo default ;;
  "context inspect --format {{{{.Endpoints.docker.Host}}}} default")
    echo unix:///var/run/docker.sock
    ;;
  "compose ps --status running --services db") echo db ;;
  "compose exec -T db pg_isready -U jobagent -d jobagent")
    count=0
    test -f "{count_file}" && count=$(tr -d '\\n' < "{count_file}")
    count=$((count + 1))
    printf '%s' "$count" > "{count_file}"
    test "$count" -ge 2
    ;;
  *) exit 9 ;;
esac""",
    )

    result = _run(env=env)

    assert result.returncode == 0
    assert "local Postgres accepts connections" in result.stdout
    assert count_file.read_text() == "2"


def test_preflight_fails_when_local_db_is_not_running(
    clone_environment: tuple[Path, dict[str, str]],
) -> None:
    _, env = clone_environment
    docker = Path(env["PATH"].split(":", maxsplit=1)[0]) / "docker"
    _executable(
        docker,
        """case "$*" in
  "--version"|"info"|"compose version") exit 0 ;;
  "context show") echo default ;;
  "context inspect --format {{.Endpoints.docker.Host}} default") echo unix:///var/run/docker.sock ;;
  "compose ps --status running --services db") exit 0 ;;
  *) exit 9 ;;
esac""",
    )

    result = _run(env=env)

    assert result.returncode == 1
    assert "docker compose up -d --wait db" in result.stderr


@pytest.mark.parametrize("remote_endpoint", ["tcp://prod.example:2376", "ssh://prod.example"])
def test_preflight_refuses_remote_docker_before_daemon_or_compose_operations(
    clone_environment: tuple[Path, dict[str, str]],
    tmp_path: Path,
    remote_endpoint: str,
) -> None:
    _, env = clone_environment
    calls = tmp_path / "docker-calls"
    docker = Path(env["PATH"].split(":", maxsplit=1)[0]) / "docker"
    _executable(
        docker,
        f"""printf '%s\\n' "$*" >> "{calls}"
case "$*" in
  "--version") exit 0 ;;
  *) exit 91 ;;
esac""",
    )
    env["DOCKER_HOST"] = remote_endpoint

    result = _run(env=env)

    assert result.returncode == 1
    assert "refusing non-local or unknown Docker endpoint" in result.stderr
    docker_calls = calls.read_text().splitlines()
    assert docker_calls == ["--version"]


def test_preflight_refuses_remote_active_docker_context(
    clone_environment: tuple[Path, dict[str, str]],
) -> None:
    _, env = clone_environment
    docker = Path(env["PATH"].split(":", maxsplit=1)[0]) / "docker"
    _executable(
        docker,
        """case "$*" in
  "--version") exit 0 ;;
  "context inspect --format {{.Endpoints.docker.Host}} production") echo ssh://prod.example ;;
  *) exit 91 ;;
esac""",
    )
    env["DOCKER_CONTEXT"] = "production"
    env["DOCKER_HOST"] = "unix:///var/run/docker.sock"

    result = _run("--skip-db", env=env)

    assert result.returncode == 1
    assert "refusing non-local or unknown Docker endpoint" in result.stderr


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("20.18.9", False),
        ("20.19.0", True),
        ("21.9.0", False),
        ("22.11.9", False),
        ("22.12.0", True),
        ("23.0.0", True),
        ("24.0.0", True),
    ],
)
def test_preflight_node_version_boundaries(
    clone_environment: tuple[Path, dict[str, str]],
    version: str,
    supported: bool,
) -> None:
    _, env = clone_environment
    node = Path(env["PATH"].split(":", maxsplit=1)[0]) / "node"
    _executable(node, f'test "$1" = "-p" && echo "{version}"')

    result = _run("--skip-db", env=env)

    assert (result.returncode == 0) is supported
    if not supported:
        assert "Node ^20.19 or >=22.12 is required" in result.stderr


def test_preflight_help_and_invalid_usage_have_documented_exit_codes() -> None:
    help_result = _run("--help", env={"PATH": "/usr/bin:/bin"})
    invalid_result = _run("--not-an-option", env={"PATH": "/usr/bin:/bin"})

    assert help_result.returncode == 0
    assert "No dependencies are installed" in help_result.stdout
    assert "uv owns Python interpreter provisioning" in help_result.stdout
    assert "0  every requested check passed" in help_result.stdout
    assert invalid_result.returncode == 2
    assert "Usage:" in invalid_result.stderr
