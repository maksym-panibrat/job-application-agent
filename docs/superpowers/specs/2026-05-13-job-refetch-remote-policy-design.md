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

- Wipe all existing job-search artifacts and rebuild them from fresh provider
  fetches.
- Preserve user identity, profile, resume, and followed company selections.
- Preserve full upstream job descriptions in storage without truncation.
- Make required in-office attendance an explicit remote-policy rule during
  matching.
- Ensure future prompt-limit increases require re-scoring, not re-fetching.

## Non-Goals

- Preserve applied, dismissed, generated, or auto-rejected application history.
  The user explicitly chose a full wipe.
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

Preserve:

- `users`
- `oauth_accounts`
- `user_profiles`
- profile-owned resume, skills, and work experience rows
- `companies`
- followed company selections on profiles
- `slug_fetches` rows, except for freshness and queue state described below

The reset must also make every non-invalid followed provider slug eligible for a
fresh crawl. For `slug_fetches` rows where `is_invalid=false`, clear:

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

The reset should run only while production workers or cron-triggered queue
drainers are paused. This avoids a worker claiming deleted applications or
writing stale rows during the wipe.

## Re-Fetch Flow

After the reset:

1. Run the existing sync entrypoint so active profiles enqueue stale followed
   company provider slugs.
2. Let the worker drain `fetch-slug` jobs.
3. Each provider adapter fetches current postings and passes full raw
   descriptions to `job_service.upsert_job`.
4. `upsert_job` stores raw and cleaned descriptions and links each job to the
   company resolved from the provider slug.
5. `match_queue_service.enqueue_for_interested_profiles` creates new
   applications for active profiles that follow the job's company.
6. Match workers score the rebuilt applications using the explicit remote
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
model allows a higher limit, the system should be able to re-score from stored
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
  in-office attendance is a hard location mismatch.
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

- If the reset aborts before commit, no partial wipe should be visible.
- If the reset succeeds but re-fetch fails for a provider slug, the existing
  `slug_fetches` retry and transient-error behavior owns recovery.
- If matching budget is exhausted after the wipe, applications may remain in
  `pending_match`; they should not be silently converted to successful matches.
- If a provider omits a description, store `NULL` or empty text as the current
  ingestion path does. Do not synthesize a description.

## Verification

Focused automated coverage:

- reset script wipes the selected job-search tables and preserves users,
  profiles, resume/profile rows, companies, and followed company IDs
- reset clears non-invalid slug freshness so sync enqueues provider slugs again
- reset leaves invalid slug rows invalid
- source/upsert path preserves descriptions longer than 20k characters
- prompt tests assert the explicit remote trust policy is present in the
  scoring prompt
- `truncate_description()` tests continue proving the cap applies at prompt
  construction, not storage

Operational verification after production execution:

- before/after row counts for wiped and preserved tables
- active profiles still have followed company IDs
- non-invalid followed slugs are queued or freshly fetched
- new `jobs` rows have long `description_raw` values when providers return them
- sampled remote-looking jobs with required office attendance are scored with a
  location/work-mode gap for remote-only profiles
