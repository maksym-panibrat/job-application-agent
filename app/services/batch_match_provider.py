from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderJobResult:
    application_id: str
    score: float | None
    summary: str
    rationale: str
    strengths: list[str]
    gaps: list[str]
    error: str | None = None


@dataclass(frozen=True)
class ProviderRequestResult:
    request_key: str
    results: list[ProviderJobResult]
    error: str | None = None


@dataclass(frozen=True)
class ProviderBatchStatus:
    ready: bool
    failed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class ProviderBatchOutput:
    requests: list[ProviderRequestResult]


class BatchMatchProvider(Protocol):
    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        raise NotImplementedError

    async def poll(self, *, provider_batch_id: str) -> ProviderBatchStatus:
        raise NotImplementedError

    async def fetch_output(self, *, provider_batch_id: str) -> ProviderBatchOutput:
        raise NotImplementedError


class FakeBatchMatchProvider:
    def __init__(
        self,
        *,
        provider_batch_id: str = "fake-provider-batch",
        ready: bool = True,
        output: ProviderBatchOutput | None = None,
    ) -> None:
        self.provider_batch_id = provider_batch_id
        self.ready = ready
        self.output = output or ProviderBatchOutput(requests=[])
        self.submitted_requests: list[dict] = []

    async def submit(self, *, requests: list[dict], display_name: str) -> str:
        self.submitted_requests = requests
        return self.provider_batch_id

    async def poll(self, *, provider_batch_id: str) -> ProviderBatchStatus:
        return ProviderBatchStatus(ready=self.ready)

    async def fetch_output(self, *, provider_batch_id: str) -> ProviderBatchOutput:
        return self.output


def get_batch_match_provider() -> BatchMatchProvider:
    from app.config import get_settings

    settings = get_settings()
    if (
        settings.environment == "test"
        or settings.batch_match_provider == "fake"
        or settings.batch_match_dry_run
    ):
        return FakeBatchMatchProvider(ready=False)
    if settings.batch_match_provider == "gemini":
        from app.services.gemini_batch_match_provider import GeminiBatchMatchProvider

        return GeminiBatchMatchProvider()
    raise ValueError(f"unknown batch match provider: {settings.batch_match_provider}")
