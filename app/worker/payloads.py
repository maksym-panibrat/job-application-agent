"""Per-job-type payload Pydantic models. Spec § Job-type taxonomy."""
import uuid

from pydantic import BaseModel


class FetchSlugPayload(BaseModel):
    provider: str
    slug: str


class MatchPayload(BaseModel):
    application_id: uuid.UUID


class GenerateCoverLetterPayload(BaseModel):
    application_id: uuid.UUID


class BatchMatchPayload(BaseModel):
    profile_id: uuid.UUID


class MaintenancePayload(BaseModel):
    date: str | None = None
