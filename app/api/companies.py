"""Company resolution endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.user_profile import UserProfile
from app.services import company_resolver

router = APIRouter(prefix="/api/companies", tags=["companies"])


class ResolveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/resolve")
async def resolve_company(
    body: ResolveRequest,
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Resolve a free-text company name to a Company row via fan-out across
    every supported ATS provider.

    Returns:
      200 — confirmed match
      400 — empty name (handled by Pydantic min_length=1)
      404 — every provider returned 404 (confirmed miss)
      503 — fan-out timed out (transient; user retries)
    """
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    try:
        company = await company_resolver.resolve(body.name, session)
    except company_resolver.FanoutTimeoutError:
        raise HTTPException(status_code=503, detail="couldn't reach our boards right now")
    if company is None:
        raise HTTPException(status_code=404, detail="company not found on any supported board")
    return {
        "company": {
            "id": str(company.id),
            "canonical_name": company.canonical_name,
            "providers": list(company.provider_slugs.keys()),
        }
    }
