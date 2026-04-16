# Skill: regression-test

**Trigger**: Use this skill whenever a bug is discovered — from a runtime error, a manual test, a user report, or a failing CI run. Also trigger when fixing any bug where no automated test currently covers the broken code path.

## What this skill does

For every bug fixed in this project, create an automated regression test **before or alongside** the fix. The test must:

1. **Reproduce the bug** (red) — write the test first so it fails without the fix
2. **Describe the failure** — include a docstring explaining what broke, the error message, and how it was discovered
3. **Pass after the fix** (green) — confirm the test passes once the fix is applied
4. **Target the right tier** — place it in the correct test directory based on what it exercises:

| What the bug involves | Test tier | Directory | Key fixtures |
|---|---|---|---|
| Pure logic (no DB, no HTTP) | Unit | `tests/unit/` | None — import and call directly |
| DB operations (services, models) | Integration | `tests/integration/` | `db_session` from `tests/integration/conftest.py` |
| Full API flow (HTTP + DB) | E2E | `tests/e2e/` | `test_app` (httpx AsyncClient) from `tests/e2e/conftest.py` |

## Test naming

- Append to an **existing** relevant test file when the bug is in an already-tested area
- Create a **new file** only when there is no existing coverage for that module
- Name format: `test_<what_was_broken>` or `test_<bug_description>_regression`
- Add a comment `# Regression: <short description of original bug>` at the top of the test

## Patterns to follow

### Unit test (no DB)
```python
def test_fromisoformat_aware_datetime():
    # Regression: JSearch posted_at produced timezone-aware datetime
    # that crashed naive TIMESTAMP column insert.
    from app.sources.jsearch import JSearchSource
    ...
```

### Integration test (real DB via testcontainers)
```python
@pytest.mark.asyncio
async def test_upsert_job_with_aware_posted_at(db_session):
    # Regression: asyncpg rejected timezone-aware posted_at datetime
    from datetime import datetime, timezone
    job_data = JobData(..., posted_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    job, created = await upsert_job(job_data, "jsearch", db_session)
    assert created is True
    assert job.posted_at.tzinfo is not None
```

### E2E test (full stack via httpx)
```python
@pytest.mark.asyncio
async def test_<feature>_regression(test_app, monkeypatch):
    # Regression: <description>
    ...
    resp = await test_app.post("/api/...")
    assert resp.status_code == 200
```

## Run commands
```bash
uv run pytest tests/unit/ -v             # fast, no DB
uv run pytest tests/integration/ -v      # requires Docker (testcontainers)
uv run pytest tests/e2e/ -v              # full stack
uv run pytest tests/ -k "regression" -v  # run only regression-tagged tests
```

## Checklist before marking done
- [ ] Test fails without the fix (reproduce first)
- [ ] Test passes after the fix
- [ ] Test is in the right tier directory
- [ ] Docstring explains the original bug and how it was found
- [ ] `uv run ruff check` passes on new test file
