"""Per-job-type payload Pydantic models. Spec § Job-type taxonomy."""
import uuid

from pydantic import BaseModel


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
    max_items: int | None = None


class MaintenancePayload(BaseModel):
    date: str | None = None
