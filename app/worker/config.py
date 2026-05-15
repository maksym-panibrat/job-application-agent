"""Worker-process configuration. Spec § Concurrency knobs."""
from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class WorkerLane:
    name: str
    job_types: tuple[str, ...] | None
    concurrency: int


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_")

    concurrency: int = 4
    llm_job_types: str | None = None
    llm_concurrency: int = 6
    slow_job_types: str | None = None
    slow_concurrency: int = 20
    poll_interval_s: int = 3
    visibility_timeout_s: int = 600
    drain_budget_s: int = 80
    transient_backoff_base_s: int = 30
    transient_backoff_max_s: int = 300
    unknown_type_backoff_s: int = 300
    mark_done_retry_backoff_s: int = 60

    @property
    def lanes_enabled(self) -> bool:
        return self.llm_job_types is not None or self.slow_job_types is not None

    def lane_configs(self) -> list[WorkerLane]:
        lanes: list[WorkerLane] = []
        if self.lanes_enabled:
            llm_types = self._parse_job_types(self.llm_job_types)
            slow_types = self._parse_job_types(self.slow_job_types)
            if llm_types:
                lanes.append(
                    WorkerLane(
                        name="llm",
                        job_types=llm_types,
                        concurrency=self.llm_concurrency,
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
