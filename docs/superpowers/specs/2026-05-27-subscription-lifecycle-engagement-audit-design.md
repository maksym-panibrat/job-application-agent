# Subscription Lifecycle and Engagement Audit Design

## Context

The existing subscription entitlement PR adds useful product behavior, but it is too narrow for a real subscription lifecycle. It stores binary entitlement state directly on `users`, treats `subscription_current_period_end` as metadata, and has no durable record of why a user's paid access, follow limit, or search status changed.

This design refactors that PR before merge. The goal is still not to add checkout, pricing UI, external billing webhooks, invoices, or a customer portal. The goal is to make the entitlement model viable for real-life operation once a billing integration is added.

## Goals

- Keep identity/auth concerns separate from subscription lifecycle state.
- Support multiple subscription tiers through data, not hard-coded plan branches.
- Represent a simple pay-upfront lifecycle: active, canceled, expired, refunded, chargeback, revoked, and none.
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
- `active`
- `created_at`
- `updated_at`

Initial rows:

- `free`: `followed_company_limit = 5`
- `paid`: `followed_company_limit = 100`

Entitlement code reads limits from this table or a small cached repository layer. Adding a tier should not require changing entitlement logic.

### `subscription_accounts`

Provider-agnostic billing account identity.

Required fields:

- `id`
- `user_id`
- `provider`, for example `manual` now, `stripe` later
- `provider_customer_id` nullable for manual subscriptions
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
- `provider_subscription_id` nullable for manual subscriptions
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

No `subscriptions` row means no paid subscription. The API may expose derived `subscription.status = "none"` for this case, but `none` is not stored in the `subscriptions` table.

### `subscription_events`

Append-only lifecycle audit. These are raw provider/admin facts, not derived entitlement decisions.

Required fields:

- `id`
- `user_id`
- `subscription_id`
- `event_type`
- `provider`
- `provider_event_id`
- `actor_user_id` nullable
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

For now these events can be created by admin/manual operations. Future Stripe webhooks should write the same event table.

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
- `actor_user_id` nullable
- `decided_at`

Decision types:

- `follow_limit_applied`
- `follow_limit_rejected`
- `search_expiry_seeded`
- `search_expiry_extended`
- `search_paused`
- `paid_entitlement_activated`
- `paid_entitlement_ended`
- `over_limit_companies_preserved`

`over_limit_companies_preserved` records the intentional behavior where a downgraded user keeps existing followed companies above the free limit, but cannot add or swap companies while still over the limit.

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

## API Shape

Keep the profile response shape stable:

```json
{
  "subscription": {
    "plan": "free",
    "status": "none",
    "paid_active": false
  },
  "limits": {
    "followed_companies": 5
  }
}
```

The backend derives this from subscription and plan services. It does not read subscription fields from `users`.

## Admin / Manual Operations

Until real billing integration lands, paid access should be controlled through a small service or admin script, not raw SQL.

Manual operations must:

1. Create or update `subscription_accounts` and `subscriptions`.
2. Insert a `subscription_events` row.
3. Recalculate effective entitlement.
4. Insert any required `entitlement_decisions`.

Supported manual operations:

- activate paid subscription through a period end
- cancel at period end
- expire subscription
- mark refunded
- mark chargeback
- revoke entitlement
- reactivate subscription
- change plan

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
