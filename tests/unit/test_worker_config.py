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
    from app.worker.config import WorkerSettings

    settings = WorkerSettings()
    assert settings.concurrency == 4
    assert settings.poll_interval_s == 3
    assert settings.visibility_timeout_s == 600
    assert settings.drain_budget_s == 80
    assert settings.transient_backoff_base_s == 30
    assert settings.transient_backoff_max_s == 300
    assert settings.unknown_type_backoff_s == 300
    assert settings.mark_done_retry_backoff_s == 60
    assert settings.llm_concurrency == 6
    assert settings.slow_concurrency == 20


def test_worker_settings_default_single_pool():
    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.lanes_enabled is False
    assert settings.lane_configs() == [
        WorkerLane(name="default", job_types=None, concurrency=settings.concurrency)
    ]


def test_worker_settings_parses_lane_job_types(monkeypatch):
    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", " match, generate-cover-letter,match ")
    monkeypatch.setenv("WORKER_LLM_CONCURRENCY", "6")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "fetch-slug, maintenance")
    monkeypatch.setenv("WORKER_SLOW_CONCURRENCY", "20")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.lanes_enabled is True
    assert settings.lane_configs() == [
        WorkerLane(
            name="llm",
            job_types=("match", "generate-cover-letter"),
            concurrency=6,
        ),
        WorkerLane(
            name="slow",
            job_types=("fetch-slug", "maintenance"),
            concurrency=20,
        ),
    ]


def test_worker_settings_blank_lane_envs_fall_back_to_default(monkeypatch):
    monkeypatch.setenv("WORKER_LLM_JOB_TYPES", " , ")
    monkeypatch.setenv("WORKER_SLOW_JOB_TYPES", "")

    from app.worker.config import WorkerLane, WorkerSettings

    settings = WorkerSettings()

    assert settings.lanes_enabled is True
    assert settings.lane_configs() == [
        WorkerLane(name="default", job_types=None, concurrency=settings.concurrency)
    ]


def test_clear_worker_env_removes_lowercase_variants(monkeypatch):
    monkeypatch.setenv("worker_llm_job_types", "match")
    monkeypatch.setenv("worker_concurrency", "99")

    _clear_worker_env(monkeypatch)

    assert "worker_llm_job_types" not in os.environ
    assert "worker_concurrency" not in os.environ


def test_clear_worker_env_removes_mixed_case_variants(monkeypatch):
    monkeypatch.setenv("WoRkEr_LlM_JoB_TyPeS", "match")
    monkeypatch.setenv("WoRkEr_CoNcUrReNcY", "99")

    _clear_worker_env(monkeypatch)

    assert "WoRkEr_LlM_JoB_TyPeS" not in os.environ
    assert "WoRkEr_CoNcUrReNcY" not in os.environ
