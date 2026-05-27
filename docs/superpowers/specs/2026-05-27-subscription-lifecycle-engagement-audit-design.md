# Subscription Lifecycle and Engagement Audit Design

## Context

The existing subscription entitlement PR adds useful product behavior, but it is too narrow for a real subscription lifecycle. It stores binary entitlement state directly on `users`, treats `subscription_current_period_end` as metadata, and has no durable record of why a user's paid access, follow limit, or search status changed.

This design refactors that PR before merge. The goal is still not to add checkout, pricing UI, external billing webhooks, invoices, or a customer portal. The goal is to make the entitlement model viable for real-life operation once a billing integration is added.

## Goals

- Keep identity/auth concerns separate from subscription lifecycle state.
- Support multiple subscription tiers through data, not hard-coded plan branches.
- Represent a simple pay-upfront lifecycle: active, canceled, expired, refunded, chargeback, and revoked.
- Preserve followed companies on downgrade while enforcing the free limit for future additions/swaps.
- Add server-authored engagement evidence for active user actions.
- Make daily maintenance the single authority for search pause/extend decisions.
- Record append-only audit rows for subscription facts, engagement facts, and entitlement decisions.
- Keep the current API response shape stable enough for the existing settings UI.

## Non-Goals

- No checkout flow.
- No billing provider webhook handler.
- No customer portal.
- No invoice, tax, coupon, or proration support.
- No trial or past-due states.
- No client analytics events driving entitlements.
- No automatic deletion of followed companies on downgrade.

## Data Model

### `users`

`users` remains identity and auth only. It must not contain canonical subscription lifecycle fields.

The current PR should remove these fields before merge:

- `subscription_plan`
- `subscription_status`
- `subscription_current_period_end`

This avoids ambiguity about the source of truth. API callers get subscription state from entitlement services, not from columns on `users`.

### `subscription_plans`

Plan/tier configuration.

Required fields:

- `id`
- `tier`, for example `free`, `paid`, later `pro` or `team`
- `display_name`
- `followed_company_limit`
- `valid_from`
- `valid_until` nullable
- `created_at`
- `updated_at`

Initial rows:

- `free`: `followed_company_limit = 5`
- `paid`: `followed_company_limit = 100`

Entitlement code reads limits from this table or a small cached repository layer. Adding a tier should not require changing entitlement logic.

A plan is selectable for new provider subscriptions when `valid_from <= now` and (`valid_until IS NULL` or `valid_until > now`). Existing subscriptions keep referencing their original plan even after `valid_until`; plan validity controls sellability, not whether an already-paid subscription remains entitled through its period end.

### `subscription_accounts`

Provider-agnostic billing account identity.

Required fields:

- `id`
- `user_id`
- `provider`, for example `stripe`
- `provider_customer_id`
- `created_at`
- `updated_at`

Constraint:

- unique `(provider, provider_customer_id)` where `provider_customer_id IS NOT NULL`

### `subscriptions`

Current lifecycle state. This is the read model for paid entitlement evaluation.

Required fields:

- `id`
- `user_id`
- `subscription_account_id`
- `plan_id`
- `provider`
- `provider_subscription_id`
- `status`
- `current_period_start`
- `current_period_end`
- `canceled_at`
- `ended_at`
- `created_at`
- `updated_at`

Allowed stored statuses:

- `active`
- `canceled`
- `expired`
- `refunded`
- `chargeback`
- `revoked`

No `subscriptions` row means no paid subscription. The API returns `subscription: null` for this case.

### `subscription_events`

Append-only lifecycle audit. These are raw provider facts, not derived entitlement decisions.

Required fields:

- `id`
- `user_id`
- `subscription_id`
- `event_type`
- `provider`
- `provider_event_id`
- `occurred_at`
- `payload` JSONB

Event types include:

- `subscription_created`
- `subscription_renewed`
- `subscription_canceled`
- `subscription_expired`
- `subscription_refunded`
- `subscription_chargeback`
- `subscription_revoked`
- `subscription_reactivated`
- `subscription_plan_changed`

The paid service/webhook integration writes this event table. Duplicate provider events are ignored by unique provider event id.

### `engagement_events`

Append-only server-authored active engagement audit. These are not client analytics.

Required fields:

- `id`
- `user_id`
- `profile_id`
- `event_type`
- `subject_type`
- `subject_id`
- `source`
- `occurred_at`
- `metadata` JSONB

Allowed active engagement event types:

- `company_followed`
- `company_unfollowed`
- `profile_updated`
- `resume_uploaded`
- `application_dismissed`
- `application_applied`
- `chat_message_sent`
- `search_resumed`

Excluded actions:

- page views
- sign-ins and logins
- match/application opens
- reading/reviewing without a state change
- background sync or match generation
- frontend analytics-only events

### `entitlement_decisions`

Append-only audit of derived decisions. This table explains state transitions caused by lifecycle and engagement facts.

Required fields:

- `id`
- `user_id`
- `profile_id`
- `decision_type`
- `previous_value` JSONB
- `next_value` JSONB
- `reason`
- `source_event_type`
- `source_event_id`
- `decided_at`

Decision types:

- `follow_limit_applied`
- `follow_limit_rejected`
- `subscription_plan_rejected`
- `search_expiry_seeded`
- `search_expiry_extended`
- `search_paused`
- `paid_entitlement_activated`
- `paid_entitlement_ended`
- `over_limit_companies_preserved`

`over_limit_companies_preserved` records the intentional behavior where a downgraded user keeps existing followed companies above the free limit, but cannot add or swap companies while still over the limit.

`subscription_plan_rejected` records an attempted provider subscription creation or plan change against a plan that is outside its validity window. Existing subscriptions are not downgraded only because their plan later becomes non-selectable.

## Entitlement Semantics

Paid entitlement is active only when:

- subscription status is `active` and `current_period_end > now`; or
- subscription status is `canceled` and `current_period_end > now`.

All other states evaluate to free entitlement:

- no subscription row
- `expired`
- `refunded`
- `chargeback`
- `revoked`
- `active` with a past `current_period_end`
- `canceled` with a past `current_period_end`

Refund and chargeback are separate statuses because they are different billing facts. They have identical entitlement behavior in this design: both immediately downgrade to free entitlement.

Downgrade behavior:

- Do not delete followed companies.
- If the user is above the free limit, allow removals.
- Reject additions and swaps while the resulting set remains above the free limit.
- Write `over_limit_companies_preserved` when a paid-to-free transition leaves the user above the free limit.

## Engagement and Search Lifecycle

State-changing API or agent actions write `engagement_events` after the action succeeds. These handlers do not directly extend free-user search expiry, except `search_resumed`, which directly reactivates search because the user is explicitly asking to resume.

Daily maintenance is the single authority for search lifecycle decisions.

For active paid-entitled profiles:

- keep `search_active = true`
- set `search_expires_at = now + search_auto_pause_days`
- write `search_expiry_extended` when the stored expiry changes materially

For active free profiles:

- if `search_expires_at IS NULL`, seed `now + search_auto_pause_days` and write `search_expiry_seeded`
- if a qualifying `engagement_events` row exists since the current expiry window began, extend to `now + search_auto_pause_days` and write `search_expiry_extended` with `reason = active_engagement`
- if no qualifying engagement exists and `search_expires_at < now`, set `search_active = false` and write `search_paused`

If a free user engages after expiry but before maintenance runs, maintenance should extend rather than pause. If maintenance already paused the search, only explicit `search_resumed` reactivates it.

## Frontend Profile API Shape

This is the authenticated frontend response for `GET /api/profile`. It is a read model for UI rendering only. Provider webhook handlers and paid-service internals do not consume this shape.

Paid active example:

```json
{
  "subscription": {
    "tier": "paid",
    "status": "active",
    "current_period_end": "2026-06-27T00:00:00Z"
  },
  "entitlements": {
    "paid_access": true,
    "search_auto_pause": false
  },
  "limits": {
    "followed_companies": 100
  }
}
```

Free/no-subscription example:

```json
{
  "subscription": null,
  "entitlements": {
    "paid_access": false,
    "search_auto_pause": true
  },
  "limits": {
    "followed_companies": 5
  }
}
```

Field usage:

- `subscription`: lifecycle summary for display. `null` means the user has no subscription row. When present, `status` is one of the stored lifecycle statuses and `current_period_end` lets the UI show renewal/cancellation-through dates.
- `entitlements.paid_access`: effective paid access flag. The UI uses this for paid/free badges and paid-only affordances without interpreting lifecycle states.
- `entitlements.search_auto_pause`: whether the UI should show free-tier auto-pause copy/countdowns. Paid-entitled users should not see auto-pause messaging. The displayed countdown is computed from `search_expires_at`, not from this boolean.
- `limits.followed_companies`: the enforced followed-company limit. The UI uses this for counters and client-side add blocking; the backend still enforces the limit.

The backend derives these fields from subscription and plan services. It does not read subscription fields from `users`.

## Refactor Current PR Before Merge

The current subscription entitlement PR should be adjusted before merge:

- Remove subscription columns from `users`.
- Replace the current migration with canonical subscription, engagement, and decision tables.
- Replace `is_paid_active(user)` with an entitlement service that loads effective subscription state.
- Make follow-limit checks use the entitlement service.
- Keep profile creation seeding `search_expires_at`.
- Record server-authored engagement events from successful state-changing endpoints.
- Make daily maintenance expire ended subscriptions, reconcile search expiry/pause, and write entitlement decisions.
- Keep frontend changes minimal by preserving the API response shape.

## Testing Requirements

Migration tests:

- subscription tables exist with expected constraints
- subscription lifecycle fields do not exist on `users`
- active profile `search_expires_at` backfill still works

Entitlement unit tests:

- `active` before period end grants paid limit
- `canceled` before period end grants paid limit
- expired plan validity does not remove paid entitlement from an existing active/canceled subscription before period end
- `expired`, `refunded`, `chargeback`, `revoked`, and no subscription grant free limit
- paid period end in the past grants free limit
- downgraded over-limit users can remove companies
- downgraded over-limit users cannot add or swap companies while still over the free limit

Engagement tests:

- successful follow, unfollow, profile update, resume upload, apply, dismiss, chat message, and search resume actions record engagement events
- page views, logins, opens, background sync, and analytics-only frontend events do not record active engagement

Maintenance tests:

- free active profile with recent engagement extends expiry
- free expired profile without engagement pauses
- free active profile with null expiry gets seeded
- paid active profile extends expiry while paid entitlement is effective
- canceled before period end keeps paid entitlement
- canceled after period end becomes free entitlement
- refunded, chargeback, and revoked downgrade immediately
- each lifecycle decision writes an `entitlement_decisions` row
- source event links are populated when applicable

## Rollout Notes

This refactor should land before the current entitlement PR merges. There is no production migration from `users.subscription_*` if the PR has not shipped yet.

If the current PR ships first, a follow-up migration must move any existing `users.subscription_*` values into the canonical tables before dropping those columns.

## Open Decisions

None. Trials and past-due handling are explicitly out of scope for this design.
