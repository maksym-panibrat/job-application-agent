"""Tests for GCP Cloud Error Reporting log plumbing in app.main.

Two invariants:
1. ERROR-level logs carry `severity: "ERROR"` and the Error Reporting `@type`
   marker so GCP Cloud Error Reporting ingests them as first-class events.
2. INFO/WARNING logs get `severity` but NOT the `@type` marker (would otherwise
   pollute Error Reporting with benign events).
3. The configure_logging() processor chain includes format_exc_info so exc_info
   tuples become a readable Python traceback string under "exception".
"""

import structlog

from app.main import _REPORTED_ERROR_TYPE, _add_cloud_run_severity, configure_logging


class _FakeSettings:
    def __init__(self, environment: str = "production", log_level: str = "INFO"):
        self.environment = environment
        self.log_level = log_level


def test_add_cloud_run_severity_tags_error_with_type():
    event = {"level": "error", "event": "cron.generation_queue.failed"}
    out = _add_cloud_run_severity(None, None, event)
    assert out["severity"] == "ERROR"
    assert out["@type"] == _REPORTED_ERROR_TYPE


def test_add_cloud_run_severity_tags_critical_with_type():
    event = {"level": "critical", "event": "fatal.shutdown"}
    out = _add_cloud_run_severity(None, None, event)
    assert out["severity"] == "CRITICAL"
    assert out["@type"] == _REPORTED_ERROR_TYPE


def test_add_cloud_run_severity_leaves_info_without_type():
    event = {"level": "info", "event": "app.startup"}
    out = _add_cloud_run_severity(None, None, event)
    assert out["severity"] == "INFO"
    # Must not tag info-level logs — they'd pollute Error Reporting.
    assert "@type" not in out


def test_add_cloud_run_severity_leaves_warning_without_type():
    event = {"level": "warning", "event": "cron.sync.budget_exhausted"}
    out = _add_cloud_run_severity(None, None, event)
    assert out["severity"] == "WARNING"
    assert "@type" not in out


def test_add_cloud_run_severity_defaults_to_info_when_level_missing():
    event = {"event": "no_level_set"}
    out = _add_cloud_run_severity(None, None, event)
    assert out["severity"] == "INFO"
    assert "@type" not in out


def test_configure_logging_includes_format_exc_info():
    # format_exc_info is required so that log.aexception / log.error(exc_info=True)
    # produces a stringified traceback under "exception" instead of a raw sys.exc_info
    # tuple (which JSONRenderer can't serialize).
    configure_logging(_FakeSettings(environment="production"))
    cfg = structlog.get_config()
    assert structlog.processors.format_exc_info in cfg["processors"]


def test_configure_logging_production_uses_cloud_run_severity():
    configure_logging(_FakeSettings(environment="production"))
    cfg = structlog.get_config()
    assert _add_cloud_run_severity in cfg["processors"]


def test_configure_logging_development_skips_cloud_run_severity():
    configure_logging(_FakeSettings(environment="development"))
    cfg = structlog.get_config()
    # ConsoleRenderer is nicer for humans; no need to tag severity for GCP.
    assert _add_cloud_run_severity not in cfg["processors"]
