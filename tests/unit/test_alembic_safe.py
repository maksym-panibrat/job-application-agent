"""Unit tests for scripts/alembic_safe.py — the prod-drift guardrail."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from scripts import alembic_safe


@pytest.fixture
def no_execvp(monkeypatch):
    """Replace os.execvp with a recorder so tests don't actually exec alembic."""
    calls: list[tuple] = []

    def _fake(path, argv):
        calls.append((path, list(argv)))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvp", _fake)
    return calls


class TestCommandClassification:
    @pytest.mark.parametrize(
        "argv",
        [
            ["upgrade", "head"],
            ["downgrade", "-1"],
            ["stamp", "abc"],
            ["merge", "x", "y"],
        ],
    )
    def test_write_commands_detected(self, argv):
        assert alembic_safe._command_is_write(argv) is True

    @pytest.mark.parametrize(
        "argv",
        [
            ["current"],
            ["history"],
            ["heads"],
            ["check"],
            ["revision", "-m", "x"],
        ],
    )
    def test_read_commands_not_treated_as_write(self, argv):
        assert alembic_safe._command_is_write(argv) is False

    def test_revision_autogenerate_is_write(self):
        assert alembic_safe._command_is_write(["revision", "--autogenerate", "-m", "x"]) is True


class TestHostGate:
    def test_blocks_neon_host_without_opt_in(self, monkeypatch, capsys, no_execvp):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-abc.neon.tech/db")
        monkeypatch.delenv("I_KNOW_ITS_PROD", raising=False)
        monkeypatch.setattr("sys.argv", ["alembic_safe.py", "upgrade", "head"])

        rc = alembic_safe.main()

        assert rc == 3
        assert no_execvp == []
        err = capsys.readouterr().err
        assert "REFUSING" in err
        assert "ep-abc.neon.tech" in err
        assert "I_KNOW_ITS_PROD=1" in err

    @pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "db"])
    def test_allows_local_hosts(self, host, monkeypatch, no_execvp):
        monkeypatch.setenv("DATABASE_URL", f"postgresql://u:p@{host}:5432/db")
        monkeypatch.setattr("sys.argv", ["alembic_safe.py", "upgrade", "head"])

        with pytest.raises(SystemExit):
            alembic_safe.main()

        assert no_execvp == [("alembic", ["alembic", "upgrade", "head"])]

    def test_passes_through_read_commands_against_prod(self, monkeypatch, no_execvp):
        # Read-only commands like `current` are safe against any host — they
        # don't mutate. This lets ops do `make migrate-status` against prod.
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-abc.neon.tech/db")
        monkeypatch.delenv("I_KNOW_ITS_PROD", raising=False)
        monkeypatch.setattr("sys.argv", ["alembic_safe.py", "current"])

        with pytest.raises(SystemExit):
            alembic_safe.main()

        assert no_execvp == [("alembic", ["alembic", "current"])]

    def test_opt_in_env_bypasses_block(self, monkeypatch, no_execvp):
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-abc.neon.tech/db")
        monkeypatch.setenv("I_KNOW_ITS_PROD", "1")
        monkeypatch.setattr("sys.argv", ["alembic_safe.py", "upgrade", "head"])

        with pytest.raises(SystemExit):
            alembic_safe.main()

        assert no_execvp == [("alembic", ["alembic", "upgrade", "head"])]

    def test_refuses_when_database_url_missing(self, monkeypatch, capsys):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Also ensure the .env fallback returns None during the test.
        with patch.object(alembic_safe, "_load_dotenv_database_url", return_value=None):
            monkeypatch.setattr("sys.argv", ["alembic_safe.py", "upgrade", "head"])
            rc = alembic_safe.main()
        assert rc == 2
        assert "DATABASE_URL is not set" in capsys.readouterr().err
