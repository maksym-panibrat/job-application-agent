# Job Re-Fetch And Remote Policy Design

## Context

Some existing jobs were fetched and scored when job descriptions were capped at
4k-8k characters. The matching cap is now 20k characters, but older rows may be
missing parts of the description that contain decisive constraints, especially
minimum in-office attendance. That creates false remote matches and damages user
trust.

This design performs a destructive job-search data repair and locks down the
matching/storage boundaries so the same repair is not needed again after a
future prompt limit increase.

## Goals

- Wipe all existing user-owned and job-search artifacts, then rebuild from a
  fresh owner account/profile.
- Preserve full upstream job descriptions in storage without truncation.
- Make required in-office attendance an explicit remote-policy rule during
  matching.
- Ensure future prompt-limit increases require re-scoring, not re-fetching.

## Non-Goals

- Preserve users, profiles, resumes, followed company selections, applied,
  dismissed, generated, or auto-rejected history. The user explicitly chose a
  full wipe because no external users are active yet.
- Add a new structured work-mode extraction schema in this pass.
- Build UI around remote policy explanations beyond the existing match gaps and
  rationale fields.
- Revalidate or resurrect provider slugs already marked invalid. Invalid slug
  repair is a separate catalog-quality task.

## Data Reset

Add or update a production-safe reset path that wipes all job-search artifacts:

- `generated_documents`
- `applications`
- `jobs`
- job-related `work_queue` rows, including `fetch-slug`, `match`, and
  `generate-cover-letter`. If `work_queue` remains job-search-only at
  implementation time, truncating the whole table is acceptable; otherwise
  delete only those job types.
- operational LLM/rate usage state currently tied to the old matching run:
  `llm_status`, `rate_limits`, and `usage_counters`
- user-owned state:
  `oauth_accounts`, `users`, `user_profiles`, `skills`, `work_experiences`,
  `events`, and LangGraph checkpoint tables if present. Checkpoints are keyed
  by profile-thread identity, so they must not survive a full user/profile
  reset. The reset path must handle the known LangGraph Postgres tables
  `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and
  `checkpoint_migrations` with `IF EXISTS` semantics because they are owned by
  `AsyncPostgresSaver`, not Alembic.

Preserve:

- `companies`
- `slug_fetches` rows, except for freshness and queue state described below

Because users/profiles are wiped, there are no followed provider slugs at reset
time. The reset must make every non-invalid provider slug in `slug_fetches`
eligible for a fresh crawl once the owner recreates their profile and follows
companies again. For `slug_fetches` rows where `is_invalid=false`, clear:

- `last_fetched_at`
- `last_attempted_at`
- `queued_at`
- `claimed_at`
- `last_status`
- `consecutive_5xx_count`

Keep `consecutive_404_count`, `is_invalid`, and `invalid_reason` unchanged so
the reset does not erase confirmed invalid-board evidence.

Rows where `is_invalid=true` stay invalid. The reset does not undo prior
invalid-board decisions.

The reset must run only while production workers or cron-triggered queue
drainers are paused. This avoids a worker claiming deleted applications or
writing stale rows during the wipe.

The reset must run as one database transaction. Use Postgres-transactional
`TRUNCATE ... CASCADE` for wiped tables plus explicit `slug_fetches` updates in
the same transaction. Print before-counts before the transaction, apply all
mutations atomically, commit, then print after-counts. If the transaction aborts,
the app must not observe a partial reset.

## Re-Fetch Flow

After the reset:

1. The owner signs in again and recreates their profile/resume/followed company
   selections.
2. Re-seed the smoke-test user before production smoke verification if the
   smoke path will be used.
3. Verify the owner profile has non-empty `target_company_ids` before any sync
   is triggered. This is the gate that defines the fresh provider-fetch scope
   after the user/profile wipe.
4. Run the existing sync entrypoint so active profiles enqueue stale followed
   company provider slugs.
5. Let the worker drain `fetch-slug` jobs.
6. Each provider adapter fetches current postings and passes full raw
   descriptions to `job_service.upsert_job`.
7. `upsert_job` stores raw and cleaned descriptions and links each job to the
   company resolved from the provider slug.
8. `match_queue_service.enqueue_for_interested_profiles` creates new
   applications for active profiles that follow the job's company.
9. Match workers score the rebuilt applications using the explicit remote
   policy below.

## Raw Description Preservation

`jobs.description_raw` is the archival upstream description for the posting.
It must be stored in full. Source adapters must not slice, summarize, clean,
or token-cap this field.

`jobs.description` is the cleaned markdown projection used for product display
and LLM prompt construction. Cleaning may remove unsafe or non-content markup
such as scripts/styles, but must not intentionally truncate substantive job
description text.

Prompt-size controls belong at the LLM boundary only. Today that boundary is
`truncate_description()` in the matching/generation agent path. If a future
model allows a higher limit, the system must be able to re-score from stored
`jobs.description` / `jobs.description_raw` without a new provider re-fetch.

Tests must include a long description above the current 20k prompt cap proving:

- ingestion stores full `description_raw`
- ingestion stores full cleaned `description`
- matching prompt construction truncates only at the LLM boundary
- the raw stored row remains longer than the LLM prompt fragment

## Remote Trust Policy

The matching prompt must make the following policy explicit:

- A job is fully remote only when the candidate can perform it without required
  recurring office attendance.
- Required in-office attendance makes the job hybrid or onsite even if provider
  metadata says `remote`.
- Phrases such as "minimum 2 days/week in office", "must work from the Toronto
  office", "hybrid schedule required", or "must be located near NYC/SF" are
  authoritative.
- For a remote-only profile (`remote_ok=true` and no target locations), required
  in-office attendance is a hard location mismatch. "Hard mismatch" means the
  score must be below `match_score_threshold`; the target score band is
  `0.0-0.29` unless the role has some exceptional partial relevance, in which
  case it still must not score high enough to appear in `pending_review`.
- For a profile with target locations, hybrid/onsite is acceptable only when
  the required office location matches one of the profile's target locations.
- If provider metadata and JD prose conflict, the JD prose wins.
- The model must decide and include the location/work-mode issue in `gaps` when
  it is a mismatch. It must not soften this into "needs clarification".

This policy stays in the scoring prompt for this pass. A future structured
classifier can promote the same rule into stored fields such as
`effective_workplace_type`, `office_attendance_required`, and
`required_office_locations` if prompt-only enforcement is not reliable enough.

## Error Handling

- If the reset aborts before commit, no partial wipe must be visible.
- If the reset succeeds but re-fetch fails for a provider slug, the existing
  `slug_fetches` retry and transient-error behavior owns recovery.
- If matching budget is exhausted after the wipe, applications may remain in
  `pending_match`; they must not be silently converted to successful matches.
- If a provider omits a description, store `NULL` or empty text as the current
  ingestion path does. Do not synthesize a description.

## Verification

Focused automated coverage:

- reset script wipes the selected job-search and user-owned tables
- reset script wipes user-owned rows and preserves companies
- reset clears all non-invalid slug freshness so sync enqueues provider slugs
  again after a new profile follows companies
- reset leaves invalid slug rows invalid
- reset script clears LangGraph checkpoint tables when present and tolerates
  their absence
- reset script is atomic: an injected failure before commit leaves all wiped
  tables and `slug_fetches` unchanged
- source/upsert path preserves descriptions longer than 20k characters
- prompt tests assert the explicit remote trust policy is present in the
  scoring prompt
- behavioral matching tests cover:
  - remote-only profile plus "minimum 3 days/week in Toronto office" scores
    below `match_score_threshold` and records a location/work-mode gap
  - Toronto-targeted profile plus the same JD is not rejected solely for work
    mode
  - provider metadata `remote` plus JD prose requiring office attendance follows
    the JD prose
- `truncate_description()` tests continue proving the cap applies at prompt
  construction, not storage

Operational verification after production execution:

- before/after row counts for wiped and preserved tables
- no pre-reset user/profile/application rows remain
- company catalog rows remain
- owner can sign in again and create a fresh profile
- owner profile has non-empty `target_company_ids` before sync is triggered
- smoke user is re-seeded before smoke tests that use `SMOKE_BEARER_TOKEN`
- non-invalid followed slugs are queued or freshly fetched
- new `jobs` rows have long `description_raw` values when providers return them
- sampled remote-looking jobs with required office attendance are scored with a
  location/work-mode gap for remote-only profiles
