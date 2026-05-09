"""Company resolution + catalog endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.deps import get_current_profile
from app.database import get_db
from app.models.company import Company
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


@router.get("/catalog")
async def get_catalog(
    profile: UserProfile = Depends(get_current_profile),
    session: AsyncSession = Depends(get_db),
):
    """Return the full curated company catalog, alphabetical by canonical_name.

    ~50 rows post-curation; sub-1KB JSON. Auth-gated for consistency with
    the rest of /api/companies, even though the data is identical for every
    caller (no per-user filtering).
    """
    rows = (
        await session.execute(
            select(Company.id, Company.canonical_name)
            .where(Company.is_curated)
            .order_by(func.lower(Company.canonical_name))
        )
    ).all()
    return [{"id": str(r.id), "canonical_name": r.canonical_name} for r in rows]
