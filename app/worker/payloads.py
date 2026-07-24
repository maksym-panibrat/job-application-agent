"""Per-job-type payload Pydantic models. Spec § Job-type taxonomy."""
import uuid

from pydantic import BaseModel, Field


class FetchSlugPayload(BaseModel):
    provider: str
    slug: str
    batch_match_max_items: int | None = None


class MatchPayload(BaseModel):
    application_id: uuid.UUID


class GenerateCoverLetterPayload(BaseModel):
    application_id: uuid.UUID


class BatchMatchPayload(BaseModel):
    profile_id: uuid.UUID
    max_items: int | None = Field(default=None, gt=0)
    # Optional lifetime candidate budget. The handler decrements this by every
    # candidate inspected so a bounded manual workflow cannot fan out forever.
    max_candidates: int | None = Field(default=None, ge=0)


class MaintenancePayload(BaseModel):
    date: str | None = None
