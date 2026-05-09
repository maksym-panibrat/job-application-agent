"""Source adapter registry.

Provider keys here are canonical: matching `SlugFetch.source`, `Job.source`,
and `Company.provider_slugs` keys, all in their post-migration shape.

Adding a new ATS provider:
  1. Implement a JobSource subclass in app/sources/<provider>.py
  2. Add it to SOURCES below.
  3. The resolver fan-out, scheduler dispatch, and slug-validation flow
     all pick it up automatically.
"""

from app.sources.ashby_board import AshbyBoardSource
from app.sources.base import JobSource
from app.sources.greenhouse_board import GreenhouseBoardSource
from app.sources.lever_postings import LeverPostingsSource

SOURCES: dict[str, JobSource] = {
    "greenhouse": GreenhouseBoardSource(),
    "lever": LeverPostingsSource(),
    "ashby": AshbyBoardSource(),
}
