"""Worker-process configuration. Spec § Concurrency knobs."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_")

    concurrency: int = 4
    poll_interval_s: int = 3
    visibility_timeout_s: int = 600
    drain_budget_s: int = 80
    transient_backoff_base_s: int = 30
    transient_backoff_max_s: int = 300
    unknown_type_backoff_s: int = 300
    mark_done_retry_backoff_s: int = 60
