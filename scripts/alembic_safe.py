"""
Wrapper around ``alembic`` that refuses to run write commands against a
non-local database unless the operator explicitly opts in.

Why this exists
---------------
We shipped a production outage because ``alembic upgrade head`` was run from a
dev laptop while ``DATABASE_URL`` in ``.env`` was still pointing at the prod
Neon instance. That advanced the prod schema (dropped a column) ahead of the
deployed code — every ``SELECT * FROM applications`` 500-ed until the matching
code change was merged and rolled out.

What it does
------------
- Parses the effective ``DATABASE_URL`` (os.environ first, then ``.env``).
- Extracts the host.
- If the host is not ``localhost`` / ``127.0.0.1`` / ``db`` (docker compose
  service name), refuses to run ``upgrade`` / ``downgrade`` / ``stamp`` /
  ``merge`` / ``revision --autogenerate`` unless ``I_KNOW_ITS_PROD=1`` is
  set in the environment.
- Read-only commands (``current``, ``history``, ``heads``, ``check``) pass
  through unchanged on any host.
- The raw ``alembic`` binary is still available for emergencies; this wrapper
  is a guardrail, not a lock.

Usage
-----
    uv run python scripts/alembic_safe.py upgrade head
    I_KNOW_ITS_PROD=1 uv run python scripts/alembic_safe.py upgrade head

Wired into the ``make migrate`` target.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

_WRITE_COMMANDS = {"upgrade", "downgrade", "stamp", "merge"}
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "db"}
_OPT_IN_ENV = "I_KNOW_ITS_PROD"


def _load_dotenv_database_url() -> str | None:
    """Minimal ``.env`` reader — avoids pulling in pydantic just for a host check."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition("=")
        if key.strip() == "DATABASE_URL":
            return value.strip().strip('"').strip("'")
    return None


def _database_host() -> str | None:
    url = os.environ.get("DATABASE_URL") or _load_dotenv_database_url()
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.hostname


def _command_is_write(argv: list[str]) -> bool:
    if not argv:
        return False
    cmd = argv[0]
    if cmd in _WRITE_COMMANDS:
        return True
    # Autogenerate issues DB introspection but does not write data — still
    # treat as write-ish because users run it against the DB they're about to
    # migrate. Only block when --autogenerate is present.
    if cmd == "revision" and "--autogenerate" in argv:
        return True
    return False


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        os.execvp("alembic", ["alembic"])

    if not _command_is_write(argv):
        os.execvp("alembic", ["alembic", *argv])

    host = _database_host()
    if host is None:
        sys.stderr.write(
            "alembic_safe: DATABASE_URL is not set — refusing to run a write command blind.\n"
        )
        return 2

    if host not in _LOCAL_HOSTS and os.environ.get(_OPT_IN_ENV) != "1":
        sys.stderr.write(
            f"\nalembic_safe: REFUSING to run `alembic {' '.join(argv)}` against "
            f"host '{host}'.\n"
            f"  This host does not look like a local database. Running write\n"
            f"  migrations against a shared/production database from a dev\n"
            f"  machine is the exact failure mode this guardrail exists for\n"
            f"  (see commit 28e5ce5 — prod outage from unintended Neon upgrade).\n\n"
            f"  If you really mean it, re-run with:\n"
            f"      {_OPT_IN_ENV}=1 ...\n\n"
            f"  For local work, point DATABASE_URL at localhost / 127.0.0.1 / db.\n"
        )
        return 3

    os.execvp("alembic", ["alembic", *argv])


if __name__ == "__main__":
    raise SystemExit(main())
