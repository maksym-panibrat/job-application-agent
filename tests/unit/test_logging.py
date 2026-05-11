"""Logging invariants for the Hetzner + Axiom era.

Pre-migration the app emitted GCP Cloud Run severity/@type fields for Error
Reporting. Post-migration (Hetzner + Vector → Axiom) those are dead weight —
errors are plain JSON records, `level=error` is the only signal Axiom needs.

Invariants:
1. configure_logging() in production produces JSON output with NO `severity`
   or `@type` fields. structlog's default `level` is enough.
2. format_exc_info stays in the processor chain so log.aexception /
   log.error(exc_info=True) renders as a readable traceback under "exception"
   (this is unrelated to GCP — Axiom benefits from it too).
3. Development still uses ConsoleRenderer; production still uses JSONRenderer.
"""

import json
import os

# Settings() validates required envs at import time (via _startup_settings in
# app.main). Set safe stubs before importing.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("GOOGLE_API_KEY", "fake-test-key")

import structlog  # noqa: E402

from app.main import configure_logging  # noqa: E402


class _FakeSettings:
    def __init__(self, environment: str = "production", log_level: str = "INFO"):
        self.environment = environment
        self.log_level = log_level


def _last_log_record(capsys) -> dict:
    """Read the last JSON line structlog emitted to stdout, return it parsed.
    structlog's JSONRenderer prints via the standard logger which goes to stdout;
    pytest's capsys captures both stdout and stderr."""
    captured = capsys.readouterr()
    lines = (captured.out + captured.err).strip().splitlines()
    assert lines, "no log lines were captured — check structlog config"
    return json.loads(lines[-1])


def test_production_log_output_has_no_gcp_severity_field(capsys):
    """The GCP `severity` field must not appear. Vector → Axiom ships
    structlog's native `level` field; `severity` is dead weight."""
    configure_logging(_FakeSettings(environment="production"))
    log = structlog.get_logger("test")
    log.error("boom", trace_id="abc")
    record = _last_log_record(capsys)
    assert "severity" not in record, f"unexpected GCP severity field: {record}"


def test_production_log_output_has_no_gcp_type_field(capsys):
    """`@type: …ReportedErrorEvent` was a Cloud Error Reporting marker.
    Axiom doesn't need it; remove the field entirely."""
    configure_logging(_FakeSettings(environment="production"))
    log = structlog.get_logger("test")
    log.error("boom")
    record = _last_log_record(capsys)
    assert "@type" not in record, f"unexpected GCP @type field: {record}"


def test_production_log_output_has_level_event_and_timestamp(capsys):
    """Sanity: structlog still emits the standard fields Axiom expects."""
    configure_logging(_FakeSettings(environment="production"))
    log = structlog.get_logger("test")
    log.error("boom", trace_id="abc")
    record = _last_log_record(capsys)
    assert record.get("level") == "error"
    assert record.get("event") == "boom"
    assert record.get("trace_id") == "abc"
    assert "timestamp" in record


def test_configure_logging_includes_format_exc_info():
    """exc_info → readable traceback string under "exception"."""
    configure_logging(_FakeSettings(environment="production"))
    cfg = structlog.get_config()
    assert structlog.processors.format_exc_info in cfg["processors"]


def test_configure_logging_production_uses_json_renderer():
    configure_logging(_FakeSettings(environment="production"))
    cfg = structlog.get_config()
    # JSONRenderer is a class instance; identity is by type.
    assert any(isinstance(p, structlog.processors.JSONRenderer) for p in cfg["processors"]), (
        "production must use JSONRenderer (Vector parses JSON)"
    )


def test_configure_logging_development_uses_console_renderer():
    configure_logging(_FakeSettings(environment="development"))
    cfg = structlog.get_config()
    assert any(isinstance(p, structlog.dev.ConsoleRenderer) for p in cfg["processors"]), (
        "development must use ConsoleRenderer (humans, not Axiom)"
    )
