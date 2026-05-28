"""Worker-process configuration. Spec § Concurrency knobs."""
import os
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_FAST_JOB_TYPES = "match,generate-cover-letter"
DEFAULT_SLOW_JOB_TYPES = "fetch-slug,maintenance,batch-match"


@dataclass(frozen=True)
class WorkerLane:
    name: str
    job_types: tuple[str, ...] | None
    concurrency: int


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_")

    concurrency: int = 4
    fast_job_types: str | None = DEFAULT_FAST_JOB_TYPES
    fast_concurrency: int = 6
    slow_job_types: str | None = DEFAULT_SLOW_JOB_TYPES
    slow_concurrency: int = 20
    poll_interval_s: int = 3
    visibility_timeout_s: int = 600
    drain_budget_s: int = 80
    transient_backoff_base_s: int = 30
    transient_backoff_max_s: int = 300
    unknown_type_backoff_s: int = 300
    mark_done_retry_backoff_s: int = 60

    def __init__(self, **data):
        if "fast_job_types" not in data and not self._env_has("WORKER_FAST_JOB_TYPES"):
            legacy_job_types = self._get_env("WORKER_LLM_JOB_TYPES")
            if legacy_job_types is not None:
                data["fast_job_types"] = legacy_job_types
        if "fast_concurrency" not in data and not self._env_has("WORKER_FAST_CONCURRENCY"):
            legacy_concurrency = self._get_env("WORKER_LLM_CONCURRENCY")
            if legacy_concurrency is not None:
                data["fast_concurrency"] = legacy_concurrency
        super().__init__(**data)

    @property
    def lanes_enabled(self) -> bool:
        return bool(
            self._parse_job_types(self.fast_job_types)
            or self._parse_job_types(self.slow_job_types)
        )

    def lane_configs(self) -> list[WorkerLane]:
        lanes: list[WorkerLane] = []
        if self.lanes_enabled:
            fast_types = self._parse_job_types(self.fast_job_types)
            slow_types = self._parse_job_types(self.slow_job_types)
            if fast_types:
                lanes.append(
                    WorkerLane(
                        name="fast",
                        job_types=fast_types,
                        concurrency=self.fast_concurrency,
                    )
                )
            if slow_types:
                lanes.append(
                    WorkerLane(
                        name="slow",
                        job_types=slow_types,
                        concurrency=self.slow_concurrency,
                    )
                )
            if lanes:
                return lanes

        return [
            WorkerLane(
                name="default",
                job_types=None,
                concurrency=self.concurrency,
            )
        ]

    @staticmethod
    def _env_has(name: str) -> bool:
        lower_name = name.lower()
        return any(key.lower() == lower_name for key in os.environ)

    @staticmethod
    def _get_env(name: str) -> str | None:
        lower_name = name.lower()
        for key, value in os.environ.items():
            if key.lower() == lower_name:
                return value
        return None

    @staticmethod
    def _parse_job_types(value: str | None) -> tuple[str, ...]:
        if value is None:
            return ()

        job_types = []
        seen = set()
        for item in value.split(","):
            job_type = item.strip()
            if not job_type or job_type in seen:
                continue
            job_types.append(job_type)
            seen.add(job_type)
        return tuple(job_types)
