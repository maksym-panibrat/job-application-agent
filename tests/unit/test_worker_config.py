import os

import pytest

WORKER_ENV_VARS = (
    "WORKER_CONCURRENCY",
    "WORKER_POLL_INTERVAL_S",
    "WORKER_VISIBILITY_TIMEOUT_S",
    "WORKER_DRAIN_BUDGET_S",
    "WORKER_TRANSIENT_BACKOFF_BASE_S",
    "WORKER_TRANSIENT_BACKOFF_MAX_S",
    "WORKER_UNKNOWN_TYPE_BACKOFF_S",
    "WORKER_MARK_DONE_RETRY_BACKOFF_S",
    "WORKER_FAST_JOB_TYPES",
    "WORKER_FAST_CONCURRENCY",
    "WORKER_LLM_JOB_TYPES",
    "WORKER_LLM_CONCURRENCY",
    "WORKER_SLOW_JOB_TYPES",
    "WORKER_SLOW_CONCURRENCY",
)


@pytest.fixture(autouse=True)
def clear_worker_env(monkeypatch):
    _clear_worker_env(monkeypatch)


def _clear_worker_env(monkeypatch):
    worker_env_names = {key.lower() for key in WORKER_ENV_VARS}
    for key in list(os.environ):
        if key.lower() in worker_env_names:
            monkeypatch.delenv(key, raising=False)


def test_worker_config_defaults():
    from app.worker.config import (
        DEFAULT_FAST_JOB_TYPES,
        DEFAULT_SLOW_JOB_TYPES,
        WorkerSettings,
    )

    settings = WorkerSettings()
    assert settings.concurrency == 4
    assert settings.poll_interval_s == 3
    assert settings.visibility_timeout_s == 600
    assert settings.drain_budget_s == 80
    assert settings.transient_backoff_base_s == 30
    assert settings.transient_backoff_max_s == 300
    assert settings.unknown_type_backoff_s == 300
    assert settings.mark_done_retry_backoff_s == 60
    assert settings.fast_job_types == DEFAULT_FAST_JOB_TYPES
    assert settings.fast_concurrency == 6
    assert settings.slow_job_types == DEFAULT_SLOW_JOB_TYPES
    assert settings.slow_concurrency == 20


def test_worker_settings_default_lanes():
    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.lanes_enabled is True
    assert settings.lane_configs() == [
        WorkerLane(
            name="fast",
            job_types=("match", "generate-cover-letter"),
            concurrency=settings.fast_concurrency,
        ),
        WorkerLane(
            name="slow",
            job_types=("fetch-slug", "maintenance", "batch-match"),
            concurrency=settings.slow_concurrency,
        ),
    ]


def test_worker_settings_parses_lane_job_types(monkeypatch):
    monkeypatch.setenv("WORKER_FAST_JOB_TYPES", " match, generate-cover-letter,match ")
    monkeypatch.setenv("WORKER_FAST_CONCURRENCY", "6")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "fetch-slug, maintenance, batch-match")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "20")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.lanes_enabled is True
    assert settings.lane_configs() == [
        WorkerLane(
            name="fast",
            job_types=("match", "generate-cover-letter"),
            concurrency=6,
        ),
        WorkerLane(
            name="slow",
            job_types=("fetch-slug", "maintenance", "batch-match"),
            concurrency=20,
        ),
    ]


def test_worker_settings_legacy_llm_env_populates_fast_lane(monkeypatch):
    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", " legacy-match, legacy-cover ")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.fast_job_types == " legacy-match, legacy-cover "
    assert settings.fast_concurrency == 2
    assert settings.lane_configs() == [
        WorkerLane(
            name="fast",
            job_types=("legacy-match", "legacy-cover"),
            concurrency=2,
        )
    ]


def test_worker_settings_fast_env_overrides_legacy_llm_env(monkeypatch):
    monkeypatch.setenv("WORKER_FAST_JOB_TYPES", " fast-match ")
    monkeypatch.setenv("WORKER_FAST_CONCURRENCY", "3")
    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", " legacy-match ")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.fast_job_types == " fast-match "
    assert settings.fast_concurrency == 3
    assert settings.lane_configs() == [
        WorkerLane(name="fast", job_types=("fast-match",), concurrency=3)
    ]


def test_worker_settings_legacy_llm_env_accepts_mixed_case_names(monkeypatch):
    monkeypatch.setenv("WoRkEr_LlM_JoB_TyPeS", " mixed-legacy ")
    monkeypatch.setenv("worker_llm_concurrency", "5")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.fast_job_types == " mixed-legacy "
    assert settings.fast_concurrency == 5
    assert settings.lane_configs() == [
        WorkerLane(name="fast", job_types=("mixed-legacy",), concurrency=5)
    ]


def test_worker_settings_blank_lane_envs_disable_lanes(monkeypatch):
    monkeypatch.setenv("WORKER_FAST_JOB_TYPES", " , ")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.lanes_enabled is False
    assert settings.lane_configs() == [
        WorkerLane(name="default", job_types=None, concurrency=settings.concurrency)
    ]


def test_clear_worker_env_removes_lowercase_variants(monkeypatch):
    monkeypatch.setenv("worker_fast_job_types", "match")
    monkeypatch.setenv("worker_concurrency", "99")

    _clear_worker_env(monkeypatch)

    assert "worker_fast_job_types" not in os.environ
    assert "worker_concurrency" not in os.environ


def test_clear_worker_env_removes_mixed_case_variants(monkeypatch):
    monkeypatch.setenv("WoRkEr_FaSt_JoB_TyPeS", "match")
    monkeypatch.setenv("WoRkEr_CoNcUrReNcY", "99")

    _clear_worker_env(monkeypatch)

    assert "WoRkEr_FaSt_JoB_TyPeS" not in os.environ
    assert "WoRkEr_CoNcUrReNcY" not in os.environ


def test_worker_defaults_use_fast_and_slow_lanes(monkeypatch):
    monkeypatch.delenv("WORKER_FAST_JOB_TYPES", raising=False)
    monkeypatch.delenv("WORKER_LLM_JOB_TYPES", raising=False)
    monkeypatch.delenv("WORKER_SLOW_JOB_TYPES", raising=False)

    from app.worker.config import WorkerSettings

    settings = WorkerSettings()
    lanes = settings.lane_configs()

    assert lanes[0].name == "fast"
    assert lanes[0].job_types == ("match", "generate-cover-letter")
    assert lanes[1].name == "slow"
    assert lanes[1].job_types == ("fetch-slug", "maintenance", "batch-match")
