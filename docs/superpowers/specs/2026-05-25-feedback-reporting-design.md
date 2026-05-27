# Feedback Reporting Design

## Summary

Add an authenticated feedback feature so users can submit product feedback and issue reports from any page. The user-facing surface is a small header entry point that opens a modal with a category selector and one text input. The backend stores every report in Postgres and optionally sends a best-effort generic webhook notification.

The database row is the source of truth. Notifications are only an alerting convenience and must never cause submitted feedback to be lost.

## Goals

- Let authenticated users submit feedback from the page where they encounter a pain point.
- Store durable feedback records with enough context for later debugging and analysis.
- Include conservative troubleshooting context automatically: identity, timestamp, current route, browser basics, viewport, timezone, and obvious route IDs.
- Notify an external receiver through a configurable generic webhook.
- Keep notification failure invisible to users when the database write succeeds.
- Keep the external notification payload minimal by default; Postgres stores the full report.

## Non-Goals

- Anonymous feedback.
- Screenshots, DOM snapshots, form values, page content, or cover-letter/job-description content capture.
- A feedback admin UI.
- Axiom-based notification routing.
- Vendor-specific Slack or email delivery in the first version.
- Analytics-event-only storage.
- Sending full feedback messages to the webhook receiver.

## User Experience

Feedback is available only when the user is authenticated.

Desktop header:
- Add a compact feedback icon button near the existing Chat and Settings controls.
- The button uses `aria-label="Send feedback"`.

Mobile menu:
- Add a `Send feedback` item to the existing action sheet.

Modal:
- Title: `Send feedback`.
- Category selector choices, in this order:
  - `Feature request`
  - `Bug`
  - `Other`
- Default category: `Feature request`.
- Text area label: `What happened?`
- Helper copy: `Page details will be included automatically.`
- Actions: `Cancel` and `Send`.
- `Send` is disabled while submission is pending and while the message is empty or whitespace-only.

Success behavior:
- Close the modal.
- Clear the draft message.
- Show a success toast: `Feedback sent`.

Failure behavior:
- Keep the modal open.
- Preserve the typed message and category.
- Show an error: `Could not send feedback. Try again.`
- If the database write succeeds but webhook notification fails, still show success to the user.

## Architecture

Backend:
- Add a `FeedbackReport` model/table.
- Import the model from `app/models/__init__.py` for SQLModel metadata and Alembic discovery.
- Add an Alembic migration for the table and indexes.
- Add `app/api/feedback.py` mounted from `app/main.py`.
- Add `app/services/feedback_service.py` to own creation and notification dispatch.
- Add settings:
  - `feedback_webhook_url: SecretStr | None = None`
  - `feedback_webhook_timeout_seconds: float = 3.0`

Frontend:
- Add `api.submitFeedback(...)` in `frontend/src/api/client.ts`.
- Add a `FeedbackModal` component.
- Open the modal from `AppShell`.
- Reuse existing UI primitives where possible: `Button`, `TextArea`, action sheet/menu patterns, and toast behavior.

Request flow:
1. User opens feedback from the header or mobile menu.
2. User selects a category and enters text.
3. Client attaches conservative diagnostics.
4. Backend authenticates the user.
5. Backend validates the request.
6. Backend writes and commits the feedback row.
7. Backend sends a best-effort webhook notification if configured.
8. Backend updates notification status in a second transaction.
9. Endpoint returns success once the database row exists.

Key invariant: notification failure must not make a saved feedback report appear failed to the user.

The endpoint may wait up to `feedback_webhook_timeout_seconds` to attempt notification and status update, but API success does not depend on notification outcome.

If the process exits after the first commit and before notification dispatch or status update, the saved row remains valid feedback. A configured webhook row may remain `pending` until an operator reviews it or a future retry mechanism is added.

## Data Contract

`feedback_reports` fields:

- `id`: UUID primary key.
- `user_id`: foreign key to `users.id`, indexed.
- `user_email`: denormalized email at report time.
- `category`: enum-like string. Allowed values are `feature_request`, `bug`, and `other`.
- `message`: required text, trimmed for validation, bounded to 1-5000 characters.
- `diagnostics`: JSON object after server-side allowlisting and size bounding.
- `notification_status`: enum-like string. Allowed values are `pending`, `not_configured`, `sent`, and `failed`.
- `notification_error`: nullable bounded string for dispatch failure details.
- `created_at`: server timestamp.

Useful indexes:
- `created_at` descending or regular btree for recent triage queries.
- `user_id` for user-centric history.
- `category` if category filtering becomes common.

Client-submitted diagnostics are conservative and bounded. The backend must allowlist these keys, validate their shape, drop unknown keys, bound string fields, and enforce a maximum serialized diagnostics size of 16 KB:

- `reported_at_client`: browser timestamp.
- `path`: `window.location.pathname + window.location.search`.
- `page_title`: `document.title`.
- `user_agent`: browser user agent.
- `viewport`: `{ "width": number, "height": number }`.
- `timezone`: `Intl.DateTimeFormat().resolvedOptions().timeZone`.
- `route_context`: parsed IDs when obvious, for example `{ "application_id": "..." }` on `/matches/:id`.

String limits:
- `reported_at_client`: 64 characters.
- `path`: 512 characters.
- `page_title`: 256 characters.
- `user_agent`: 512 characters.
- `timezone`: 128 characters.
- `route_context` keys and values: 64 keys maximum, 128 characters per key or value.

If diagnostics are missing, store `{}`. If diagnostics include unknown keys, drop them. If the sanitized diagnostics object is still larger than 16 KB serialized JSON, reject the request with a validation error instead of storing a partial oversized object.

The client should submit path/query rather than full absolute URL. The server derives authenticated `user_id` and `user_email`; it must not trust client-submitted identity.

Category labels map to wire values as follows:

| UI label | API value |
| --- | --- |
| `Feature request` | `feature_request` |
| `Bug` | `bug` |
| `Other` | `other` |

Data access and lifecycle:
- Feedback records are operator data for product triage and debugging.
- V1 access is through direct database queries or agent/operator tooling, not an in-app admin UI.
- Records are retained indefinitely in v1 unless a user/account deletion workflow removes the associated user data.
- Future admin tooling should preserve the same privacy boundary: show the full message from the database, not from notification payloads.

## API

Endpoint: `POST /api/feedback`

Authentication:
- Required.
- Uses the same authenticated user dependency as `/api/users/me`.

Request shape:

```json
{
  "category": "feature_request",
  "message": "I want a way to hide companies I already rejected.",
  "diagnostics": {
    "reported_at_client": "2026-05-25T20:15:00.000Z",
    "path": "/matches?status=pending",
    "page_title": "Job Search",
    "user_agent": "Mozilla/5.0 ...",
    "viewport": { "width": 1440, "height": 900 },
    "timezone": "America/Los_Angeles",
    "route_context": {}
  }
}
```

Success response:

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "created": true,
  "notification_status": "sent"
}
```

`notification_status` in the success response may be `pending`, `not_configured`, `sent`, or `failed`. The frontend must treat all successful `2xx` responses as user-visible success.

Validation:
- Reject unauthenticated requests.
- Reject empty or whitespace-only `message`.
- Reject `message` longer than the 5000-character maximum.
- Reject categories other than `feature_request`, `bug`, and `other`.
- Accept missing diagnostics by storing an empty object.
- Allowlist diagnostics keys, validate expected shapes, drop unknown keys, apply field-specific string limits, and reject sanitized diagnostics over 16 KB serialized JSON.

## Notification Webhook

Notification dispatch runs after the database commit.

If `feedback_webhook_url` is unset:
- Do not call out.
- Store `notification_status="not_configured"`.

If `feedback_webhook_url` is set:
- Insert the row with `notification_status="pending"`.
- POST a compact JSON payload to the configured URL after the first commit.
- Use a short timeout.
- On 2xx, store `notification_status="sent"` in a second transaction.
- On non-2xx, timeout, or network error, store `notification_status="failed"` and a bounded `notification_error` in a second transaction.
- If the second transaction fails, log `feedback.notification_status_update_failed` and still return success because the row already exists.
- Do not fail the API response if the feedback row was created.

Webhook payload:

```json
{
  "event": "feedback.submitted",
  "feedback_id": "00000000-0000-0000-0000-000000000000",
  "category": "feature_request",
  "message_preview": "I want a way to hide companies I already rejected.",
  "user_id": "00000000-0000-0000-0000-000000000001",
  "user_email": "user@example.com",
  "path": "/matches?status=pending",
  "created_at": "2026-05-25T20:15:01.000000+00:00",
  "diagnostics": {
    "page_title": "Job Search",
    "viewport": { "width": 1440, "height": 900 },
    "timezone": "America/Los_Angeles",
    "route_context": {}
  }
}
```

The webhook payload does not include the full feedback message in v1. `message_preview` is derived from the start of the message and bounded to 240 characters. The full message remains in Postgres.

Structured logs:
- `feedback.submitted`
- `feedback.notification_sent`
- `feedback.notification_failed`
- `feedback.notification_status_update_failed`

## Testing

Backend integration tests:
- Authenticated submit creates a `feedback_reports` row.
- Empty message is rejected.
- Invalid category is rejected.
- Missing auth is rejected.
- Missing webhook config succeeds and records `not_configured`.
- Webhook success records `sent`.
- Webhook failure still returns success and records `failed` with a bounded error.
- Webhook status-update failure still returns success and logs `feedback.notification_status_update_failed`.
- Diagnostics unknown keys are dropped and sanitized oversized diagnostics are rejected.
- Webhook payload excludes the full `message` field and includes bounded `message_preview`.

Frontend tests:
- Desktop header exposes the feedback entry point.
- Mobile menu exposes the feedback entry point.
- Modal renders category choices in the approved order.
- Modal maps category labels to `feature_request`, `bug`, and `other`.
- Empty message cannot submit.
- Submit sends category, message, and conservative diagnostics.
- Successful submit closes the modal and shows `Feedback sent`.
- API failure preserves typed text and shows an error.
- Successful submit ignores `notification_status` for user-facing success/error state.

Regression checks:
- Existing auth, shell, route, and API tests remain green.
- No page content or form values are included in diagnostics.

## Open Decisions

No open product decisions remain for the first version.

Implementation can still choose exact component file names and icon shape based on existing frontend conventions.
