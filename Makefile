# Makefile — developer convenience targets for job-application-agent.
#
# Prerequisites: uv, Python 3.12+, DATABASE_URL in env or .env
#
# Quick start:
#   make smoke-token        # print a 90-day JWT for smoke@panibrat.com
#   make seed-smoke-user    # seed smoke user into DATABASE_URL
#   make smoke              # run golden-path smoke test (needs SMOKE_BASE_URL + SMOKE_BEARER_TOKEN)

.PHONY: migrate migrate-status smoke-token seed-smoke-user smoke help

# ---------------------------------------------------------------------------
# migrate
#
# Thin wrapper over alembic that refuses to run write migrations against a
# non-local database unless you pass I_KNOW_ITS_PROD=1.  Prevents the foot-gun
# of running `alembic upgrade head` from a dev laptop while DATABASE_URL still
# points at prod Neon (the exact cause of commit 28e5ce5's outage).
#
# Usage:
#   make migrate ARGS="upgrade head"
#   make migrate ARGS="downgrade -1"
#   I_KNOW_ITS_PROD=1 make migrate ARGS="upgrade head"   # explicit opt-in
#   make migrate-status                                   # read-only check
# ---------------------------------------------------------------------------
migrate:
	uv run python scripts/alembic_safe.py $(ARGS)

migrate-status:
	uv run python scripts/alembic_safe.py current


# ---------------------------------------------------------------------------
# smoke-token
#
# Signs a 90-day JWT for the seeded smoke user (smoke@panibrat.com) using the
# same secret and claims shape that fastapi-users / app/api/deps.py expects:
#   - alg: HS256
#   - aud: ["fastapi-users:auth"]
#   - sub: SMOKE_USER_ID (stable UUID from scripts/seed_smoke_user.py)
#
# Usage:
#   JWT_SECRET=<prod-secret> make smoke-token
#   # or rely on DATABASE_URL + .env to load JWT_SECRET via pydantic settings
#
# Store the printed token as the SMOKE_BEARER_TOKEN GitHub Actions secret.
# ---------------------------------------------------------------------------
smoke-token:
	uv run python scripts/make_smoke_token.py

# ---------------------------------------------------------------------------
# seed-smoke-user
#
# Idempotently creates the smoke@panibrat.com user in the database pointed to
# by DATABASE_URL.  Safe to re-run.
# ---------------------------------------------------------------------------
seed-smoke-user:
	uv run python scripts/seed_smoke_user.py

# ---------------------------------------------------------------------------
# smoke
#
# Run the golden-path smoke test.
# Requires: SMOKE_BASE_URL, SMOKE_BEARER_TOKEN, and SMOKE_CRON_SECRET to be set.
# ---------------------------------------------------------------------------
smoke:
	uv run python scripts/smoke/golden_path.py

# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
help:
	@echo "Available targets:"
	@echo "  smoke-token      Print a 90-day JWT for smoke@panibrat.com"
	@echo "  seed-smoke-user  Seed smoke user into DATABASE_URL (idempotent)"
	@echo "  smoke            Run golden-path smoke test against SMOKE_BASE_URL"
	@echo ""
	@echo "Required env vars for smoke-token:"
	@echo "  JWT_SECRET (or set via .env / DATABASE_URL so pydantic loads it)"
	@echo ""
	@echo "Required env vars for smoke:"
	@echo "  SMOKE_BASE_URL       e.g. https://api-xxx-uc.a.run.app"
	@echo "  SMOKE_BEARER_TOKEN   JWT from make smoke-token"
	@echo "  SMOKE_CRON_SECRET    Value of CRON_SHARED_SECRET prod secret"
