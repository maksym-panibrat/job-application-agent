# Subscription Entitlements Design

## Problem

The app needs a paid subscription boundary without adding a billing provider yet. Free users should be limited to a small followed-company set, while paid users can follow many more companies and should not be auto-paused for inactivity.

The current inactivity behavior has a gap: `search_expires_at` is only set when a user manually resumes search through `PATCH /api/profile/search`. New profiles are created with `search_active=true` and `search_expires_at=NULL`, and maintenance explicitly leaves those profiles alone. That means the intended 7-day auto-pause window is not applied unless the user first toggles search.

This spec adds entitlement storage and enforcement only. Real checkout, payment webhooks, pricing pages, and external subscription sync are follow-up work.

## Goals

- Add explicit account-level subscription fields that can be updated manually or by future billing integration.
- Seed new free profiles with a 7-day active search window on profile creation.
- Keep free users limited to 5 followed companies.
- Allow active paid users to follow up to 100 companies.
- Keep paid users active by extending their search expiry during maintenance instead of pausing them.
- Enforce company limits server-side in every current path that can mutate followed companies.
- Keep match generation unrestricted by subscription; there is no daily match quota in this design.

## Non-Goals

- Do not integrate Stripe, Paddle, Lemon Squeezy, or any other billing provider.
- Do not add checkout, customer portal, invoices, emails, pricing UI, or webhook handlers.
- Do not limit daily matches, visible feed size, LLM scoring volume, manual sync count, or cover-letter generation based on subscription.
- Do not add admin UI for subscription management in this pass.
- Do not change matching score rules, worker queue semantics, provider adapters, or company resolution behavior except for enforcing follow limits.

## Decisions

| Topic | Choice |
|---|---|
| Billing scope | Entitlements only. Future billing code updates local user fields. |
| Entitlement owner | `users`, not `user_profiles`, because subscription is account-level. |
| Free company limit | 5 followed company IDs. |
| Paid company limit | 100 followed company IDs when subscription is active. |
| Match quota | None. The earlier 20 matches/day idea is out of scope. |
| Free inactivity | New profiles and resumed free searches expire after `search_auto_pause_days` days, currently 7. |
| Paid inactivity | Active paid users are never paused by maintenance; maintenance extends `search_expires_at` by another 7 days. |
| Paid expiry representation | Keep `search_expires_at` populated for observability instead of setting it to `NULL`. |
| Unknown subscription states | Treat anything other than active paid as free for enforcement. |

## Data Model

Add subscription fields to `users`:

| Column | Type | Notes |
|---|---|---|
| `subscription_plan` | `TEXT NOT NULL DEFAULT 'free'` | Local plan identifier. Initial values: `free`, `paid`. |
| `subscription_status` | `TEXT NOT NULL DEFAULT 'inactive'` | Local status identifier. Initial values: `inactive`, `active`. |
| `subscription_current_period_end` | `TIMESTAMPTZ NULL` | Optional future billing compatibility field. Not required for active entitlement in this pass. |

An account is paid-active only when `subscription_plan = 'paid'` and `subscription_status = 'active'`. Any other combination uses free limits and free pause behavior.

`subscription_current_period_end` is metadata only in this pass. Do not auto-expire paid access from that timestamp until real billing integration owns subscription synchronization and stale-status handling.

No new profile fields are required. `UserProfile.search_active`, `UserProfile.search_expires_at`, and `UserProfile.target_company_ids` remain the operational fields.

## Entitlement Helpers

Add a small entitlement service, for example `app/services/entitlements.py`, that centralizes:

- `is_paid_active(user) -> bool`
- `company_follow_limit(user) -> int`
- `next_search_expiry(now, settings) -> datetime`
- `dedupe_company_ids(company_ids) -> list[uuid.UUID]`
- `validate_company_follow_change(user, current_ids, requested_ids) -> list[uuid.UUID]`

This keeps product behavior independent from future billing-provider code. Stripe or another provider should later update `users.subscription_*`; it should not reimplement company or pause policy.

`validate_company_follow_change` returns the deduped requested IDs when the change is allowed. It raises a 4xx-safe domain error when the requested change exceeds the entitlement limit or violates downgraded-account cleanup rules.

Use `HTTP 422` for company-limit violations. The request is authenticated and authorized, but the submitted profile shape is invalid for the account's current entitlement.

## Profile Creation

`profile_service.get_or_create_profile(user_id, session)` currently creates `UserProfile(user_id=user_id)` without a search expiry. It should load the owning `User` before creation and seed:

- `search_active = true`
- `search_expires_at = now + search_auto_pause_days`

This applies to every new profile, including free users and active paid users. Paid users will then keep rolling forward through maintenance.

The concurrent-create recovery path stays the same: if insert races and fails with `IntegrityError`, rollback and re-read the profile. The expiry is only set on the actual create path, not on every read.

## Search Pause Lifecycle

`PATCH /api/profile/search` remains the explicit pause/resume endpoint.

When pausing:

- Set `search_active = false`.
- Clear `search_expires_at` to `NULL`. A paused search has no active expiry window; resuming creates a new one.

When resuming:

- Set `search_active = true`.
- Set `search_expires_at = now + search_auto_pause_days` for both free and paid users.

`run_daily_maintenance()` should load the owning `User` for each active profile. For each active profile:

- If the user is paid-active, keep `search_active = true` and set `search_expires_at = now + search_auto_pause_days`.
- If the user is not paid-active and `search_expires_at < now`, set `search_active = false`.
- If the user is not paid-active and `search_expires_at >= now`, leave it unchanged.
- If the user is not paid-active and `search_expires_at IS NULL`, set `search_expires_at = now + search_auto_pause_days`.

Profiles with `search_active = true` and `search_expires_at = NULL` are legacy data. Maintenance should not silently leave them active forever, but it also should not pause existing users immediately on deploy. It sets a fresh free expiry once for free users and rolls paid-active users forward like any other paid profile.

## Company Follow Limits

Company follow limits apply to the number of IDs in `UserProfile.target_company_ids`.

Server-side enforcement points:

- `PATCH /api/profile` when `target_company_ids` is present.
- `app.agents.onboarding.persist_inferred_companies()`, which appends resolved companies during chat onboarding.
- Any test helper or smoke helper that mutates company IDs through production services should use the same service path or deliberately bypass it with a comment.

`PATCH /api/profile` currently receives only `profile` and `session`. The implementation should also depend on `get_current_user` or load the `User` by `profile.user_id` before enforcing company limits. `persist_inferred_companies()` receives only `profile`, so it must load the owning user by `profile.user_id` before validating the append.

The enforcement should dedupe company IDs before counting them so duplicate client payloads do not artificially exceed limits or persist duplicates. The stored array should preserve the user's visible order after dedupe.

If a user downgrades from paid to free while following more than 5 companies:

- Do not automatically remove existing followed companies.
- Block adding new companies while the resulting count is above the free limit.
- Allow removals even when the resulting count is still above the free limit.
- Allow replacing the full list when the new set is a strict subset of the existing set, because that is a removal-only cleanup.
- Allow any replacement whose resulting count is within the user's current limit.
- Reject replacements that both introduce a new company ID and leave the resulting count above the user's current limit.

This avoids destructive entitlement side effects while still enforcing the current plan for future mutations.

The limit check therefore needs both the current stored company IDs and the requested company IDs. A simple `len(requested_ids) <= limit` check is not enough for downgraded accounts because it would block progressive cleanup from 100 companies down to 99. Conversely, a simple "count decreased" check is not enough because it would allow replacing 100 existing companies with 99 entirely new companies while still above the free limit.

## API Response Shape

`GET /api/profile` should include lightweight entitlement metadata so the frontend can show limits without duplicating policy:

```json
{
  "subscription": {
    "plan": "free",
    "status": "inactive",
    "paid_active": false
  },
  "limits": {
    "followed_companies": 5
  }
}
```

These field names are part of the implementation contract for this feature. Future billing work can add fields, but it should not rename these without a separate compatibility change.

`GET /api/profile` currently depends on `get_current_profile`; it must also obtain the current `User` or load the user by `profile.user_id` to populate this metadata from the account-level fields.

When company-limit enforcement fails, return a clear JSON error, for example:

```json
{
  "detail": "Free accounts can follow up to 5 companies."
}
```

## Frontend Behavior

The settings followed-companies section should read the effective limit from the profile response.

Expected behavior:

- Show the current count and limit near the followed companies control.
- Disable or block the add input when the current count is at the limit.
- Keep remove actions enabled even when over the current limit after a downgrade.
- Surface server errors from profile PATCH and onboarding company persistence without hiding the failed save.
- Do not add checkout or upgrade flows in this pass; a short "paid accounts can follow up to 100 companies" note is enough if the UI needs context.

Search UI should continue using `search_active` and `search_expires_at`. For paid-active users, hide the "Auto-pause in N days" copy because paid users are extended by maintenance and should not see a free-inactivity warning.

## Operational Management

Because this pass is entitlements-only, subscription changes are manual database updates or script-driven updates.

Example operator actions:

- Activate paid: set `subscription_plan='paid'`, `subscription_status='active'`.
- Deactivate paid: set `subscription_status='inactive'`.

The implementation should include a small documented script or README section only if needed for safe operation. It should not print secrets or require sourcing `.env`.

## Migration

One Alembic revision adds the new `users.subscription_*` columns.

Backfill behavior:

- Existing users default to free/inactive.
- Existing active profiles with `search_expires_at IS NULL` should receive an initial expiry of `now + search_auto_pause_days` rather than staying immortal.
- Existing inactive profiles with `search_expires_at IS NULL` should remain paused with no expiry.
- Existing profiles with a future expiry keep it.
- Existing profiles with an expired expiry remain eligible to be paused by the next maintenance run unless they are later marked paid-active.

The implementation plan should account for PostgreSQL locking risk. Adding text columns with static defaults is acceptable. Any data backfill for `user_profiles.search_expires_at` should be a separate statement from schema changes.

## Error Handling

- Invalid subscription field values should be prevented by application-level constants and database check constraints for the initial `free`/`paid` and `inactive`/`active` values.
- Company-limit errors should not partially persist profile changes.
- Onboarding company persistence should append only when the final deduped set is within the effective limit.
- Maintenance should log paid extensions and free pauses with profile and user IDs.
- If a profile has no owning user, maintenance should skip it and log a warning rather than crashing the whole daily task.

## Tests

Backend:

- `get_or_create_profile()` creates a profile with `search_expires_at` set about 7 days in the future.
- Concurrent profile creation still recovers after `IntegrityError`.
- Free users cannot save more than 5 company IDs through profile PATCH.
- Paid-active users can save up to 100 company IDs.
- Users with inactive paid fields are treated as free.
- `subscription_current_period_end` does not grant or remove paid access in this entitlements-only pass.
- Duplicate company IDs are deduped before limit enforcement.
- A downgraded over-limit user can save a removal-only subset even when still above 5 companies.
- A downgraded over-limit user cannot introduce any new company while still above 5 companies.
- Onboarding company persistence respects the same limits.
- Maintenance pauses expired free users.
- Maintenance extends expired or near-expired paid-active users by another 7 days.
- Maintenance backfills legacy active free profiles with `search_expires_at=NULL` to a fresh 7-day expiry.
- Maintenance leaves inactive profiles with `search_expires_at=NULL` paused.
- Profile PATCH returns `422` for company-limit violations and does not partially persist other profile fields from that failed request.

Frontend:

- Followed companies UI displays the effective count and limit.
- Add input is blocked at the free limit.
- Remove remains available at or above the limit.
- Server limit errors are shown.
- Paid-active profile fixtures use the 100-company limit.

Migration:

- Alembic upgrade adds subscription fields with defaults.
- Existing users are free/inactive.
- Existing active null profile expiries are backfilled.
- Existing inactive null profile expiries stay null.

## Rollout

1. Deploy schema and code together.
2. Verify new users receive a non-null `search_expires_at`.
3. Verify a free user with 5 followed companies cannot add a sixth.
4. Manually mark a test user paid-active and verify they can follow more than 5 companies.
5. Run maintenance and verify paid-active search expiry extends instead of pausing.

No external billing system depends on this rollout. Future payment integration should only need to update `users.subscription_plan`, `users.subscription_status`, and optionally `users.subscription_current_period_end`.
