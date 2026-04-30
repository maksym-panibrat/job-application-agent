# Matching prompt redesign — design

**Date:** 2026-04-29
**Status:** Approved (brainstorming)
**Owner:** Maksym Panibratenko

## Problem

The current matching prompt (`app/agents/matching_agent.py:86`) and pipeline have three distinct issues:

1. **Output low signal-to-noise.** The LLM returns `rationale` (free-form prose) and `strengths` (a list that mostly re-states the candidate's profile). On reviewed matches the user reads `gaps` first because it carries decision-relevant info; the rest is filler.
2. **Profile renderer is missing data.** `format_profile_text()` (`app/services/match_service.py:22`) emits `"Open to remote: yes"` but never includes `target_locations`. A real-world failure: a Seattle hybrid role was flagged with a "may require clarification regarding relocation" gap, even though the user's profile lists CA cities + remote. The LLM literally couldn't see the cities.
3. **Job descriptions are bloated.** Greenhouse stores raw HTML in `Job.description_md` (column is misnamed). 22% of average size is just tags, with outliers up to 75% tags. The current 8000-char cap truncates **41% of jobs in prod (1,564 / 3,811)**, often mid-content.

The user also wants the output to be more concise (no filler, no prose) and to consume fewer tokens overall, while keeping audit traceability.

## Goals

- Replace `rationale` (UI-displayed) with a 1-line **job summary** describing what the role IS.
- Make `strengths` describe **JD requirements the candidate meets** (not generic profile skill names).
- Keep `gaps` as the highest-value field; tighten its format.
- Keep a short **audit-purpose rationale** persisted in DB AND structured-logged.
- Fix the location data leak: include `target_locations` in profile text and pass JD location/workplace_type as structured fields.
- Pre-clean HTML to markdown at ingestion time (new column), preserving structure for the LLM and reducing input tokens for free.
- Keep the existing 8000-char input cap (post-cleaning); skip raising it (rate-limit risk on Gemini Flash TPM).

## Non-goals

- Rename `Job.description_md` → `description_html` (legacy misnomer; do in a follow-up PR).
- Add explicit prompt caching API calls. Gemini implicit prefix caching already applies to the stable system+profile prefix.
- Section-aware filtering (drop "About us" / "Benefits" / "EOE"). Defer until metrics show the simpler fix is insufficient.
- Generation agent prompt changes — out of scope.

## Design

### 1. New tool schema (LLM output)

`record_score` tool args become:

| Field | Type | Constraint | Storage | Purpose |
|---|---|---|---|---|
| `score` | float | 0.0–1.0 | `match_score` | UI badge + threshold |
| `summary` | str | ≤12 words | `match_summary` (NEW) | UI: "what is this job?" |
| `strengths` | list[str] | 1–3 items, ≤8 words each | `match_strengths` | UI: "what fits" |
| `gaps` | list[str] | 1–3 items, ≤8 words each | `match_gaps` | UI: "what's missing" |
| `rationale` | str | ≤20 words | `match_rationale` (kept) + structlog | Audit |

Both `match_summary` (display) and `match_rationale` (audit) are persisted so they're queryable from SQL — but `match_rationale` is no longer surfaced in the UI.

### 2. Prompt rewrite — split into SystemMessage + HumanMessage

**SystemMessage (stable across the fan-out batch — Gemini implicit cache prefix):**

```
Score how the candidate profile matches the job (0.0–1.0).

Grading:
- 0.9–1.0: meets all required + most preferred
- 0.7–0.89: meets all required, some preferred gaps
- 0.5–0.69: meets most required, notable gaps
- 0.3–0.49: meets some required, major gaps
- 0.0–0.29: fundamental mismatch

Location:
- JD location ∈ candidate locations OR (JD remote AND candidate remote): not a gap.
- Otherwise: hard gap, e.g., "Onsite Seattle, candidate based in CA".
- Never say "may require clarification" or "depends". Decide.

Output (call record_score):
- summary: ≤12 words. The JOB: level, stack, mode. No prose.
- strengths: 1-3 JD requirements the candidate meets. ≤8 words each. No filler.
- gaps: 1-3 weak/missing JD requirements. ≤8 words each. No filler.
- rationale: ≤20 words. Why this score (audit).
```

**HumanMessage (variable per call):**

```
PROFILE:
{profile_text}

JOB: {title} @ {company}
Location: {location} · {workplace_type}
{description}
```

`{description}` is `description_clean` (markdown) when present, else `description_md` (raw HTML) as fallback during the backfill window.

### 3. Profile text includes locations (always)

`format_profile_text()` adds an explicit Locations line, rendered even when empty/false so the LLM never has to infer:

```
Locations: San Francisco, San Jose, Los Angeles; remote: yes
```

(For "remote-only" profiles: `Locations: (none); remote: yes`. For "no remote": `Locations: …; remote: no`.)

### 4. Pre-clean job descriptions at ingestion

**New module:** `app/services/html_cleaner.py`

```python
from markdownify import markdownify

def clean_html_to_markdown(html: str) -> str:
    """Convert raw HTML to compact markdown; strips script/style; collapses extra newlines."""
    md = markdownify(html or "", heading_style="ATX", strip=["script", "style"])
    return re.sub(r"\n{3,}", "\n\n", md).strip()
```

**New column:** `Job.description_clean: str | None` (markdown). Original `description_md` (raw HTML) is preserved for UI display and future re-cleaning.

**Where it runs:** `app/services/job_sync_service.py` calls `clean_html_to_markdown()` whenever it writes `description_md`.

**Backfill:** `scripts/backfill_job_description_clean.py` walks all `description_clean IS NULL` rows, computes the clean version, batch-commits in chunks of 200.

**Spike data (n=50, stratified across 5 size buckets):**

| Strategy | mean size | jobs ≤8k | avg savings | structure |
|---|---:|---:|---:|---|
| raw | 7,455 | 30/50 | — | full HTML |
| regex_strip | 5,969 | 38/50 | 19% | none |
| bs4_text | 5,913 | 38/50 | 20% | partial |
| **markdownify** | 6,202 | 38/50 | 16% | **headings, lists, bold preserved** |

Size differences are tiny (4 pp); structure differences are major. Markdownify wins.

The existing 8000-char cap stays. After cleaning, ~10–15% of jobs (vs. 41% today) will still exceed it; those are the bottom-quality bucket anyway.

### 5. Job context carries structured location

`JobContext` (`matching_agent.py:55`) gains two fields populated from the existing `Job` columns:

```python
class JobContext(TypedDict):
    application_id: str
    title: str
    company: str
    location: str | None
    workplace_type: str | None  # remote | hybrid | onsite | None
    description: str  # description_clean ?? description_md
```

`match_service.score_and_match()` populates them when building the list passed to the graph.

## Architecture diagram

```
Ingestion path (new):
  Greenhouse → job_sync_service.upsert_job
                 ├─ description_md (raw HTML, kept)
                 └─ description_clean = clean_html_to_markdown(html)  ← NEW

Matching path:
  match_service.score_and_match
    ├─ format_profile_text(profile, …)        ← now includes Locations line
    └─ build JobContext per job                ← +location, +workplace_type, description=clean ?? raw
        └─ matching_agent.build_graph
              ├─ SystemMessage (rubric, location rule, output rules)  ← new, cacheable prefix
              └─ HumanMessage (profile, job header, description)
                    └─ record_score tool call → ScoreResult
                         └─ persisted: score, summary, strengths, gaps, rationale
                         └─ logged:    rationale (audit)
```

## Data flow per matched job (DB + API + UI)

```
ScoreResult
  ├─ score      → applications.match_score      → MatchCard ScoreBadge
  ├─ summary    → applications.match_summary    → MatchCard 1-line text   ← was rationale
  ├─ strengths  → applications.match_strengths  → MatchCard + Review list
  ├─ gaps       → applications.match_gaps       → MatchCard + Review list
  └─ rationale  → applications.match_rationale  → (no UI) + structlog match.scored
```

## Files to touch

### Backend
- `alembic/versions/<new>_add_job_description_clean.py` — `description_clean: text NULL`
- `alembic/versions/<new>_add_application_match_summary.py` — `match_summary: text NULL`
- `app/models/job.py` — `description_clean: str | None`
- `app/models/application.py` — `match_summary: str | None`
- `app/services/html_cleaner.py` — new module
- `app/services/job_sync_service.py` — invoke `clean_html_to_markdown` on description writes
- `app/services/match_service.py` — `format_profile_text()` adds Locations; build `JobContext` with `location`, `workplace_type`, `description=clean ?? raw`; persist `match_summary`
- `app/agents/matching_agent.py` — `SystemMessage` + `HumanMessage` split, new prompt, `record_score` tool args (+ `summary`), `ScoreResult` (+ `summary`), `JobContext` (+ `location`, `workplace_type`)
- `app/agents/test_llm.py` — fake response includes `summary`
- `app/api/applications.py` — serialize `match_summary` in both endpoints
- `scripts/backfill_job_description_clean.py` — one-off backfill

### Frontend
- `frontend/src/api/client.ts` — `match_summary: string | null`
- `frontend/src/components/MatchCard.tsx` — replace `match_rationale` block with `match_summary` (single line, no clamp)
- `frontend/src/components/MatchCard.test.tsx` — fixture + assertion update
- `frontend/src/pages/ApplicationReview.tsx` — replace `match_rationale` text (line :224) with `match_summary`; rationale no longer rendered

### Tests
- `tests/unit/test_html_cleaner.py` — golden fixtures: HTML lists/headings/inline tags → expected markdown
- `tests/unit/test_matching_agent.py` — assert tool args include `summary`; assert `JobContext` carries location fields; assert `format_profile_text` includes Locations line
- `tests/integration/test_match_service.py` — assert `match_summary` persisted on a scored row
- `tests/integration/test_job_sync_service.py` — assert `description_clean` populated on insert
- `tests/integration/test_backfill_description_clean.py` — assert backfill script processes NULL rows

## Migration / rollout plan

1. Land migrations + code in a single PR. Both new columns are nullable — no backfill required to deploy.
2. Post-deploy: run `scripts/backfill_job_description_clean.py` once on prod (`make migrate` is for schema; the backfill is a one-off Python script invoked from CI or via Cloud Run job).
3. Until backfill completes, matching falls back to raw `description_md` (no behavior regression for unprocessed rows).
4. After all rows have `description_clean`, monitor `match.scored` logs for one week — verify rationale stays under the 20-word cap, no spike in `match.rate_limit_skip`.
5. **Follow-up PR (separate):** rename `description_md` → `description_html` (column + model + sync code).

## Risks

| Risk | Mitigation |
|---|---|
| Markdownify mangles unusual HTML (e.g., raw tables, base64 images) | Golden tests cover common Greenhouse patterns; on exception, fall back to raw HTML so we never lose content |
| New column on `jobs` table requires `make migrate` against Neon — outage mode of commit `28e5ce5` if run from a laptop | Use `make migrate ARGS="upgrade head"` locally only against local DB; prod migration runs in CI `migrate` job per CLAUDE.md |
| Frontend cache: users with stale JS will see `null` for `match_summary` until refresh | Single-tenant app; brief refresh; no action needed |
| Output cap too tight → LLM truncates mid-thought | The grading rubric and per-field word caps are calibrated to ~80 output tokens; if Flash ignores the cap, tighten with explicit `max_tokens` later |
| Rate-limit headroom shrinks if input cap is later raised | Out of scope for this PR; revisit only if `match.rate_limit_skip` rises post-rollout |

## Test strategy

- **Unit:** `test_html_cleaner.py` (golden HTML → markdown), `test_matching_agent.py` (prompt structure, tool args, `JobContext` shape), profile rendering tests assert Locations line.
- **Integration:** `test_match_service.py` end-to-end with FakeListChatModel returning the new tool schema; assert `match_summary` persisted; assert profile text includes Locations.
- **Smoke (`tests/smoke/`):** existing matching smoke continues to pass.
- **Manual spot-check:** run `scripts/compare_jd_cleaning.py` (already in repo) on a fresh sample after deploy; sanity-check artifact diffs.

## Out of scope (explicit)

- Section-aware JD filtering ("About us" / "Benefits" stripping).
- Bumping `MAX_JOB_DESC_CHARS` above 8000.
- Renaming `description_md` → `description_html`.
- Generation-agent prompt changes.
- Replacing `match_rationale` column or migrating its old contents.
