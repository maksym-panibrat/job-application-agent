def test_worker_config_defaults(monkeypatch):
    for key in (
        "WORKER_CONCURRENCY",
        "WORKER_POLL_INTERVAL_S",
        "WORKER_VISIBILITY_TIMEOUT_S",
        "WORKER_DRAIN_BUDGET_S",
        "WORKER_TRANSIENT_BACKOFF_BASE_S",
        "WORKER_TRANSIENT_BACKOFF_MAX_S",
        "WORKER_UNKNOWN_TYPE_BACKOFF_S",
        "WORKER_MARK_DONE_RETRY_BACKOFF_S",
    ):
        monkeypatch.delenv(key, raising=False)

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
