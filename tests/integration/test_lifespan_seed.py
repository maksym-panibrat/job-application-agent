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

    # `app` is a process-global FastAPI instance shared with every other test.
    # Lifespan exit closes the AsyncConnectionPool the checkpointer wraps but
    # leaves the AsyncPostgresSaver attached to app.state — so anything later
    # in the same pytest process that reads app.state.checkpointer (e2e
    # tests/e2e/test_chat_flow.py go down the chat code path that uses it)
    # would hit a closed pool and emit "Stream error". Snapshot before, restore
    # after. Cleanup is unconditional — if the test body raises, the next test
    # still gets a clean app.state.
    # `app` is a process-global FastAPI instance shared with every other test.
    # Lifespan exit closes the AsyncConnectionPool the checkpointer wraps but
    # leaves the AsyncPostgresSaver attached to app.state — so anything later
    # in the same pytest process that reads app.state.checkpointer (e2e
    # tests/e2e/test_chat_flow.py go down the chat code path that uses it)
    # would hit a closed pool and emit "Stream error". Snapshot before, restore
    # after. Cleanup is unconditional — if the test body raises, the next test
    # still gets a clean app.state.
    prior = getattr(app.state, "checkpointer", None)
    try:
        async with LifespanManager(app):
            rows = (
                (await db_session.execute(select(Company).where(Company.is_curated)))
                .scalars()
                .all()
            )
    finally:
        if prior is None:
            if hasattr(app.state, "checkpointer"):
                delattr(app.state, "checkpointer")
        else:
            app.state.checkpointer = prior

    assert len(rows) >= 1, "lifespan completed but no curated rows landed"
    assert any(r.canonical_name == "Stripe" for r in rows), (
        "Stripe is in companies.yaml; if it stops appearing, the catalog "
        "either wasn't seeded or the YAML drifted"
    )
