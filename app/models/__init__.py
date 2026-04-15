# Import all models here so that SQLModel.metadata is fully populated
# and alembic autogenerate can discover every table.

from app.models.application import Application, GeneratedDocument  # noqa: F401
from app.models.job import Job  # noqa: F401
from app.models.search_cache import JobSearchCache  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.user_profile import Skill, UserProfile, WorkExperience  # noqa: F401

__all__ = [
    "User",
    "UserProfile",
    "Skill",
    "WorkExperience",
    "Job",
    "Application",
    "GeneratedDocument",
    "JobSearchCache",
]
