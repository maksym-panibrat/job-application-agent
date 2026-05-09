"""Smoke check: booting FastAPI via lifespan actually seeds the curated catalog.

Every other catalog test calls `seed_catalog(db_session)` directly. This one
goes through the real `lifespan` context manager, so a future change that
moves the seed call after `yield`, removes it, or breaks the lazy import
inside lifespan would fail this test.
"""

from asgi_lifespan import LifespanManager
from sqlmodel import select

from app.models.company import Company


async def test_lifespan_runs_seed_catalog(db_session):
    # `patch_settings` (autouse) already points DATABASE_URL at the testcontainer
    # and resets the cached settings/engine singletons. `db_session` has already
    # created the schema. Importing `app.main` constructs the FastAPI app whose
    # lifespan we want to exercise.
    from app.main import app

    async with LifespanManager(app):
        rows = (await db_session.execute(select(Company).where(Company.is_curated))).scalars().all()

    assert len(rows) >= 1, "lifespan completed but no curated rows landed"
    assert any(r.canonical_name == "Stripe" for r in rows), (
        "Stripe is in companies.yaml; if it stops appearing, the catalog "
        "either wasn't seeded or the YAML drifted"
    )
