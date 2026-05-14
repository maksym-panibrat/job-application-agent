# Frontend UX redesign

> Historical design document. Several operational references in this file
> predate the deployed worker-queue migration and Hetzner deployment. Do not use
> it as the current runtime contract; use `README.md`, `CLAUDE.md`,
> `docs/DEPLOYMENT.md`, `app/api/internal_cron.py`, and `app/worker/` instead.

**Date:** 2026-05-06
**Status:** Draft for review
**Author:** Maksym Panibratenko (with Claude)

## Context

The current frontend (React + Vite + TanStack Query + Tailwind, no design tokens) ships, but the day-to-day UX has accumulated pain points that make the app feel like an internal tool rather than a polished product:

- The match card's "Review →" link and "Dismiss" button sit in the same top-right corner, both small, and easy to mis-tap on mobile — accidental dismissals happen.
- The card body itself is not tappable — only the small "Review →" link navigates. Counter-intuitive on mobile.
- The job description (the most important content on the match-detail page) is hidden behind a collapsible "Job Details" expander.
- The current profile data on the Onboarding page is also collapsed by default — the user can't see what the agent extracted without opening it.
- After uploading a resume or chatting with the agent (both of which mutate the profile), the user has to navigate back to Matches and click "Sync jobs" manually for changes to take effect. The implicit "Sync now" handoff is never spoken.
- Visual polish is thin: default Tailwind palette, no design tokens, no consistent spacing/typography rhythm. Reads as unfinished.
- There's no instrumentation. We can't tell which surfaces are used, where users drop off, or what's worth fixing next.

This spec covers a full UX overhaul of the frontend: design system, IA, every page, and a small in-app event log so future iteration is data-informed instead of guessed.

## Decisions (locked during brainstorming)

| Topic | Choice |
|---|---|
| Scope | Full UX overhaul (not targeted fixes) |
| Visual direction | "Modern Product" — Linear/Vercel feel, dark by default |
| Theme | Dark only (light mode deferred; tokens shaped to allow it later) |
| Device priority | Mobile-first |
| Information architecture | Single feed with status filter chips; chat in hamburger / icon |
| Match card pattern | Whole-card tap → detail; swipe-left to dismiss; kebab `⋯` menu as desktop / accessibility fallback |
| Auto-sync behavior | Resume upload → silent immediate sync; chat profile mutations → inline "Search now" CTA in agent reply; profile-completeness gates the very first sync; periodic backend cron is the safety net |
| First-run | Profile-completeness card on the feed with a deterministic checklist — no forced wizard, no tooltip tour |
| Analytics | Tiny in-app event log (`events` table in the existing Postgres) + a small set of SQL views read from Neon's web console — no SaaS, no third party |
| Implementation approach | Incremental page-by-page migration in small PRs (no long-lived branch, no feature flags) |
| "Mark as applied" | Removed as a separate action — clicking "Open posting ↗" optimistically marks the application as `applied` and opens the URL. A "Move back to pending" entry in the kebab handles correction. |

## Sync cadence (factual reference)

The backend cron (`.github/workflows/cron.yml`, `app/scheduler/tasks.py`) actually does:

| Cron | Cadence | Purpose |
|---|---|---|
| `run_job_sync` | every 6h | Sweep `search_active=True` profiles; prune invalid slugs; enqueue stale slugs for fetching |
| `run_sync_queue` | every 5min | Drain slug fetch queue (actual Greenhouse pulls) |
| `run_match_queue` | every 5min | Score newly fetched jobs against profiles |
| `run_generation_queue` | every 10min | Generate cover letters for `pending` apps |
| `run_daily_maintenance` | daily 03:00 UTC | Mark stale jobs; auto-pause searches expired beyond 7d |

So discovery is bounded by a 6h sweep, but once anything is queued it processes within minutes. Manual `Sync now` calls `/api/jobs/sync` directly and bypasses the 6h wait.

User-facing copy must use **vague intervals** ("every few hours", "soon", "in a few minutes") to avoid lying about precise schedules and to leave room to shift cadence later.

## Approach: incremental page-by-page migration

Build the design tokens + primitive components first, then migrate one route at a time in small PRs. No long-lived branch, no `?v2=1` flag plumbing — once a page is migrated, it's the new page. Existing tests (Vitest unit + Playwright e2e) catch regressions. Single-developer portfolio context means temporary inconsistency between migrated and not-yet-migrated routes is acceptable.

Migration order:

1. **Design tokens + Tailwind config extension** (no visible change yet; new utilities available)
2. **Primitive components** in `src/components/ui/` (Button, Chip, Card, ActionSheet, Drawer, Toast, EmptyState, TextField, SwipeableCard) + colocated tests
3. **App shell** (header, hamburger condensation, BudgetBanner restyle)
4. **Feed page** (`/`) — replaces `/matches`; profile-completeness card; status chips; redesigned MatchCard; sticky sync row; pull-to-refresh
5. **Match detail** (`/matches/:id`) — full-width description, match-analysis surface, redesigned cover-letter editor, sticky bottom action bar; "Open posting" auto-marks applied
6. **Settings page** (`/settings`) — replaces `/profile`; structured form for deterministic settings; CTA into Coach for nuanced fields
7. **Coach drawer** (global, deep-linkable via `?coach=1`) — restyle existing chat into the Drawer primitive; inline `Search now` CTA in agent replies; pre-prompted opens
8. **Analytics ingest** — `events` table migration, `POST /api/events` endpoint, `src/lib/track.ts` wrapper, instrument every event in the canon
9. **SQL views** — `scripts/analytics_views.sql`; one-time apply via `psql`
10. **Cleanup** — delete `Applied.tsx`, `Onboarding.tsx`, the old `MatchCard.tsx`, the standalone `/applied` and `/profile` routes; redirect `/matches` → `/`

Each step is a PR. Tests adjusted alongside the code. Several steps can run in parallel on different working branches if convenient (e.g., 8/9 are independent of 4–7).

## Section 1: Design system / tokens

A small, codified token layer in `src/styles/tokens.css` and a `tailwind.config.js` extension. Every component reaches for tokens, never raw `bg-blue-600`.

**Color tokens** (HSL CSS variables, prefixed `--c-`):

- `--c-bg` `#0b0d12` (page) · `--c-surface` `#11141b` (cards) · `--c-surface-2` `#1a1e28` (hover, elevated)
- `--c-border` `#1f2330` (default) · `--c-border-strong` `#2a2f3d` (focus, selected)
- `--c-text` `#f9fafb` · `--c-text-muted` `#9ca3af` · `--c-text-subtle` `#6b7280`
- `--c-accent` `#a78bfa` (purple — primary CTA, focus rings, selection) · `--c-accent-fg` `#0b0d12`
- `--c-success` `#4ade80` (high match, applied confirmations)
- `--c-warning` `#fbbf24` (gaps, paused, near-budget)
- `--c-danger` `#ef4444` (dismiss, errors, exhausted)

**Typography**: Inter (body) + ui-monospace (meta, IDs, dates). Tailwind size scale at 1.2 ratio: `text-xs` (12px) → `text-2xl` (24px). Headings get `letter-spacing: -0.01em`. Body line-height 1.5; meta line-height 1.4.

**Spacing**: Tailwind defaults; everything snaps to the 4px grid (`p-2` / `p-3` / `p-4` / `p-6` / `p-8`).

**Radii**: `--r-sm` 6px (badges), `--r-md` 10px (buttons, chips), `--r-lg` 14px (cards), `--r-pill` 999px (chips, score badges).

**Shadows**: `--sh-1` (subtle, popovers/sheets), `--sh-2` (drawers); cards use borders, not shadows, in dark mode.

**Motion**: 150ms ease-out for state changes, 250ms for sheets/drawers. `@media (prefers-reduced-motion: reduce)` disables transforms; opacity transitions stay.

**Touch targets**: every interactive element ≥ 44×44 CSS pixels. Padding-inflated where visible size is smaller.

## Section 2: Primitive components

Live in `src/components/ui/`. Each is a thin React component with colocated `.test.tsx`. No headless-UI / Radix dependency — the set is small enough to hand-roll. No icon library — SVGs go in `ui/icons/` as inline components.

- **Button** — `<Button variant="primary|secondary|ghost|destructive" size="sm|md|lg" pending>`. Sizes correspond to 32 / 40 / 48px heights, all with ≥ 44px tap including padding. `pending` shows in-place spinner.
- **IconButton** — always 44×44. `aria-label` is a TS-required prop.
- **Chip** — single-select toggle with optional `count` baked in. Used for status filter row.
- **Badge** — non-interactive label (success / warning / danger / muted). Color is meaning, not decoration.
- **Card** — base surface. `interactive=true` adds hover border + cursor-pointer; `as="a"` makes it a real anchor (preserves long-press / middle-click / right-click).
- **ActionSheet** — bottom sheet on mobile, centered popover on desktop. Backdrop tap dismisses; focus-trapped while open; `Escape` dismisses.
- **Drawer** — full-height side panel. Slides from right on desktop (420px), takes over viewport on mobile. Same focus-trap rules.
- **Toast** — fire-and-forget. Auto-dismiss 5s. Stacks bottom-right (desktop) / bottom-center (mobile). Single `<ToastProvider>` at app root; consumed via `useToast()`.
- **EmptyState** — shared layout for "nothing here yet" surfaces (icon, title, description, optional CTAs).
- **TextField / TextArea** — auto-resize textarea. `<label htmlFor>` always present. `aria-invalid` on error.
- **SkeletonLine / SkeletonCard** — promote existing informal skeletons.
- **SwipeableCard** — wraps `Card`. Pointer-event swipe with 24px commit threshold; spring-back on release. Disabled when `prefers-reduced-motion`. Kebab fallback always available.

## Section 3: IA & routing

```
/                               → Feed (single)
  ?status=pending  (default)
  ?status=applied
  ?status=dismissed
  ?coach=1                      (drawer overlay; orthogonal to ?status)
/matches/:id                    → Match detail (full page)
/settings                       → Settings
/login                          → Landing (Google sign-in + dev-login)
/auth/callback                  → OAuth callback
```

**Routing rules**:

- Status filter is a **query param**, not a nested route — chip switch swaps the filtered list in place; deep-link `?status=applied&coach=1` recreates state on load.
- Match detail keeps its own route — cover-letter editor needs space, browser back navigates back to feed with scroll restoration.
- Coach drawer is **URL-driven** (`?coach=1`) so it's deep-linkable and back-button-closeable.

**App shell header** (single, all routes):

```
Job Agent              ⚙   ✦   ≡
```

- Logo → `/`
- ⚙ Settings → `/settings` (icon-only on mobile, "Settings" text on desktop)
- ✦ Coach → toggles `?coach=1`
- ≡ Hamburger (mobile-only, ≥md collapses to inline icons) — sheet contains Settings, Coach, Sign out

**Auth gating**: `RequireAuth` wraps `/`, `/matches/:id`, `/settings`. Redirects to `/login` if no token.

**What this kills from the current routes**:

- `/matches` → renamed to `/`
- `/applied` → folded into `?status=applied`
- `/profile` → renamed to `/settings`; chat-only fields move to the Coach drawer

## Section 4: Feed page

The single most-touched screen.

**Layout (mobile, top-down)**:

```
[ AppShell header ]
[ Profile-completeness card ]   ← only when profile incomplete OR search paused
[ Status chips row + Sync button ]   ← sticky on scroll
[ Match cards … ]
```

**Profile-completeness card** — gates the first sync; surfaces sync state thereafter. Four states:

1. **Setup** — any required field missing (resume, target_roles, target_locations, search_keywords). Shows checklist; each unchecked row has a `Tell coach →` button that opens the drawer pre-prompted (`?coach=1&prompt=set_locations`). Footer: "Search will start automatically when these are set." Card invalidates the `['profile']` TanStack Query key on Coach drawer close, on `uploadResume` success, and on `updateProfile` success — ensuring checks toggle as soon as state actually changes.
2. **Ready / first-sync** — all checks done. Card flips to "Profile ready · Start search" button. Tap triggers `triggerSync` and the card collapses.
3. **Paused** — `search_active=false` (manual or 7d auto-pause). Warning amber treatment. "Search is paused — Resume search" with the last-results relative time.
4. **Healthy / hidden** — profile complete and search active → card doesn't render.

The card is dismissible only in state 4 (where it doesn't render anyway). In states 1–3 it stays put — that's the point.

**Status chips** (Section 2 primitive) — Pending / Applied / Dismissed. Default Pending. URL-driven via `?status=`. Counts come from a single `/api/applications/counts` request the page makes alongside the list.

**Sticky chip row**: chips + Sync button stick to the top after the profile card scrolls off, so Sync is always one tap away on mobile.

**Sync button + state**: replaces the existing `SyncStatusChip` + bare button combination. Inlined live state into the button:

- Idle: `Sync now` (secondary style)
- Syncing: `Searching… 3 of 12 boards` (disabled, copy live)
- Scoring: `Scoring 8 jobs…`
- Success: toast `✓ N new matches` (replaces the old green inline banner)
- Idle helper line below: "Last synced **a few minutes ago** · we re-check every few hours" — vague intervals.

**Pull-to-refresh** (mobile only): drag-to-refresh maps to the same `triggerSync` action.

**Match card** (the chosen pattern):

- Whole card is `<a href="/matches/:id">` — real link, real keyboard, real right-click / middle-click.
- Top row: ScoreBadge (left) + relative posting age (right, monospace) + kebab `⋯` IconButton (44×44, top-right corner, far from natural tap zone).
- Title (15px / 700) → company → location · workplace · salary line in monospace muted.
- Footer (single line, `─` divider): top strength + top gap, each truncated to one line. Full lists live on detail.
- GenerationBadge inline next to score badge when present.
- Swipe-left reveals red `Dismiss` action; second tap commits (no instant destruction).
- Kebab opens ActionSheet: `Save for later · Open original posting ↗ · Dismiss`.
- Optimistic dismissal with **undo toast** ("Dismissed Senior Backend Engineer · Undo") — 5s window, then commits.

**Empty / loading states**:

- Profile incomplete + no apps → only the profile card renders, with a one-line "We'll show matches here once your search starts."
- Profile complete + no pending → EmptyState: "Caught up · We'll surface new matches as boards refresh · Sync now."
- Loading → SkeletonCards.

**Where InvalidSlugsNotice goes**: moves *into* the Settings page. The feed is for matches; pruned slugs are settings-plane concerns.

**BudgetBanner** stays at the top of the AppShell, unchanged.

## Section 5: Match detail + cover-letter editor

The "hidden behind expander" complaint dies here. Description is the centerpiece.

**Layout (mobile, top-down)**:

```
[ ← Back            ⋯ ]   sticky header
[ Acme Robotics ]
[ Senior Backend Engineer ]
[ berlin · hybrid · €90k–110k · 3d ]
[ Match analysis card — accent-bordered ]
   87% match
   one-sentence summary
   ───────
   Strengths        Gaps
   (full lists, no truncation)
[ ──── Job description ──── ]
   Full markdown render (no expander)
[ ──── Cover letter ──── ]
   Editor or Generate CTA
[ ⏷ Skip   |   Open posting ↗ ]   sticky bottom (mobile only)
```

**Header**: `← Back` uses `navigate(-1)` (preserves feed scroll). `⋯` opens the same kebab ActionSheet as the card (`Save · Open original ↗ · Dismiss`); entries hide when not applicable.

**Hero**: company is small/muted; title gets `text-2xl`/700; meta line in monospace, gracefully shrinks when fields are missing (no empty `· ·`).

**Match analysis block**: distinct surface (`bg-surface-2` + 3px accent left-border) so it reads as the agent's verdict. Score in success/warning/muted color band; one-sentence summary; strengths/gaps two-column on desktop, stacked on mobile, **full lists**.

**Job description block**: section divider header, full-width markdown render of `description_md`. No expander, no `max-h-96` clipping. If absurdly long (>~3000 chars), a "Read more" fade at ~800px expands inline; default state is full.

**Cover letter block**:

- No cover letter yet → primary `Generate cover letter` button + helper "Takes about 30 seconds." Pending spinner while running.
- Generation failed → red toast + inline retry CTA: "Last attempt failed. Try again."
- Generated → editor with toolbar row: provenance line ("Edited · gemini-2.5-pro") + actions: `Save edits` (disabled when pristine) · `Download PDF ↓` · `Regenerate` in the kebab (destructive, requires confirm).
- Textarea: full-width, `min-h-[400px]`, monospace, autosave-on-blur in addition to explicit Save. Saved confirmation: "Save edits" → "✓ Saved" inline for 2s. No toast in the editor.
- Mobile keyboard: textarea grows to fit; sticky bottom bar lifts above the keyboard via `viewport-fit=cover` + `env(safe-area-inset-bottom)`.

**Sticky bottom action bar** (mobile only):

- `⏷ Skip` (ghost) → opens dismiss-confirm sheet.
- `Open posting ↗` (primary) → optimistically calls `markApplied(id)` + opens `apply_url` in new tab. If `markApplied` fails, error toast "Couldn't mark as applied — Retry"; the new tab is already open by then.
- Once `applied`: bar shows "✓ Applied · Open posting again ↗" (subtle success tint). Kebab gains "Move back to pending" so accidental clicks can be undone.

**Desktop layout (≥md)**: single column, `max-w-3xl`. Sticky bottom bar collapses; actions move inline below the description block. Strengths/gaps stay two-column.

**Loading**: skeleton for hero + match analysis + ~6 lines of description. Cover letter section appears empty (it's an action, not content yet).

## Section 6: Settings + Coach drawer + Onboarding-as-chat

### Settings (`/settings`)

Replaces `/profile`. Structured form for deterministic things, link to Coach for nuanced things. No more "chat is the only way to set X."

Sections:

- **Search**: Active / Paused toggle (calls existing `toggleSearch(active)`); live countdown of `search_expires_at` ("Auto-pause in N days").
- **Resume**: file name + uploaded relative time + `Re-upload` button (triggers silent sync) + `Open in new tab` (link to a backend endpoint that returns the latest stored resume; if no such endpoint exists today, scope-deferred to a follow-up — see Out of scope).
- **Target boards**: editable chip list per provider (Greenhouse / Lever / Ashby), `+ Add slug` input. Calls `updateProfile({ target_company_slugs })`. Today only the agent writes these; the structured editor is new.
- **Pruned recently**: the existing `InvalidSlugsNotice` content, lifted out of the feed and into Settings. Per-slug Dismiss persists client-side (no backend change).
- **Profile** (read-only summary): roles, locations, salary, skills count, work-experience count. Footer CTA `✦ Open Coach` (opens the drawer pre-prompted with `"I want to change my profile"`).
- **Account**: email + `Sign out`.

### Coach drawer (`?coach=1`)

The existing chat experience, restyled into the Drawer primitive. Three changes from today:

1. **Inline action buttons in agent replies** — when an agent reply includes a profile mutation, render a `[✦ Search now]` button under the message. Click → `triggerSync()` → toast "Searching now". This requires both a small backend change (the chat endpoint must emit a structured marker on the SSE stream when the agent has mutated the profile during the turn — see "Open implementation detail" below) and frontend code to parse the marker and render the CTA.
2. **Pre-prompted opens** — opening with `?coach=1&prompt=<slug>` autofills (does not auto-send) a starter message. Used by:
   - Profile-completeness card row CTAs (`prompt=set_locations`, `prompt=set_salary`, etc.)
   - Settings → Open Coach CTA (`prompt=change_profile`)
3. **Sticky composer + scrollable history** — composer hugs keyboard via `env(safe-area-inset-bottom)`. Resume upload button stays inline next to the composer.

Deliberately **not** in the Coach: search-active toggle (Settings), PDF download (match detail), unsolicited "agent suggestions". The agent acts only when prompted.

### Onboarding (no separate page)

There is no `/onboarding` route. The first-run flow IS:

1. New user signs in → lands on `/`.
2. `getProfile()` returns mostly-empty → Profile-completeness card renders in **Setup** mode.
3. User taps a row's `Tell coach →` → drawer opens pre-prompted.
4. As fields fill, checks toggle in real time (card refetches profile on Coach close + on relevant API mutations).
5. When all required items are checked, card flips to **Ready**: `Profile ready · Start search` button. Click triggers `triggerSync` and the card collapses to a muted status row.
6. New matches appear in the feed; toast `✓ N new matches`. Card disappears.

No wizard. No tour. Same UI handles first-run and steady-state.

### Open implementation detail: agent → frontend "mutation marker"

Today the chat endpoint streams plain text. To render the inline `Search now` button under a mutation reply, the agent's reply needs to signal that a profile mutation happened. Two options:

- **(a)** Append a structured trailer to the SSE stream (`event: meta\ndata: {"profile_mutated":true}\n\n`) parsed by the client.
- **(b)** Have the client refetch `getProfile()` on every assistant reply completion and diff against pre-message state — render the CTA when fields differed.

Default: **(a)** — explicit, cheap, and avoids a profile fetch per turn. Goes into Step 7 of the migration order (Coach drawer migration).

## Section 7: Analytics — tiny event log + SQL views

### Backend

**Model** (`app/models/event.py`, registered in `app/models/__init__.py`):

```python
class Event(SQLModel, table=True):
    __tablename__ = "events"
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    profile_id: UUID | None = Field(foreign_key="user_profiles.id", index=True)
    session_id: str = Field(index=True, max_length=64)
    name: str = Field(index=True, max_length=64)
    properties: dict | None = Field(default=None, sa_column=Column(JSONB))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)
    user_agent: str | None = Field(default=None, max_length=512)
    path: str | None = Field(default=None, max_length=256)
```

Indices: `(profile_id, occurred_at)`, `(name, occurred_at)`, `(session_id, occurred_at)`. Per CLAUDE.md, declared explicitly via `sa_column=Column(JSONB)`.

**Endpoint** (`app/api/events.py`):

```python
@router.post("/api/events", status_code=204)
async def log_events(
    body: EventBatchIn,
    request: Request,
    user: User | None = Depends(maybe_authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    profile_id = user.profile_id if user else None
    ua = (request.headers.get("user-agent") or "")[:512]
    rows = [
        Event(
            profile_id=profile_id, session_id=body.session_id,
            name=ev.name, properties=ev.properties,
            user_agent=ua, path=ev.path,
        )
        for ev in body.events[:50]
    ]
    session.add_all(rows)
    await session.commit()
```

- Optional auth: pre-login events still land with `profile_id=null`.
- Batched ingest (cap 50 per request, drop overflow silently).
- Rate-limited via the existing `rate_limit_service` in production, keyed by `(profile_id or session_id)`.
- Returns 204 — fire-and-forget.

**Retention**: extend `run_daily_maintenance` to delete `events` rows older than 90 days.

### Frontend

`src/lib/track.ts` — single file, public API `track(name, properties?)`. Buffers events; flushes every 5s and on `pagehide`. Uses `keepalive: true` so pending requests survive a tab close. Errors are swallowed — analytics never break the app.

`session_id` is a random UUID stored in `sessionStorage` per browser session.

### Event taxonomy

Naming convention `<surface>.<action>`. Past tense for events; non-past for state observations.

Auth / lifecycle: `auth.signin_clicked`, `auth.signin_succeeded`, `auth.signin_failed`, `auth.signed_out`, `app.error_boundary_hit`.

Profile setup: `profile.completeness_viewed`, `profile.coach_opened_from_card`, `profile.first_sync_started`, `profile.search_paused`, `profile.search_resumed`.

Feed: `feed.viewed`, `feed.status_filter_changed`, `feed.sync_clicked`, `feed.sync_succeeded`, `feed.sync_failed`, `feed.empty_state_shown`.

Match: `match.card_opened`, `match.dismissed`, `match.dismiss_undone`, `match.applied`, `match.unapplied`, `match.original_posting_opened`, `match.swipe_attempted`, `match.kebab_opened`.

Cover letter: `cover_letter.generation_clicked`, `cover_letter.generation_succeeded`, `cover_letter.generation_failed`, `cover_letter.edited` (deduped to first keystroke per session per doc), `cover_letter.saved`, `cover_letter.pdf_downloaded`, `cover_letter.regenerated`.

Coach: `coach.opened`, `coach.message_sent`, `coach.message_failed`, `coach.search_now_clicked`, `coach.closed`.

Settings: `settings.viewed`, `settings.search_toggled`, `settings.resume_uploaded`, `settings.slug_added`, `settings.slug_removed`.

Property shapes are JSONB free-form per event but consistent within an event name (documented inline in `track.ts` via TS types).

### SQL views

Land in `scripts/analytics_views.sql`, applied once via `psql $DATABASE_URL -f scripts/analytics_views.sql`. Re-runnable (`CREATE OR REPLACE VIEW`).

Views:

- **`analytics_onboarding_funnel`** — per-profile MIN-occurred-at for: `auth.signin_succeeded`, `profile.coach_opened_from_card`, `profile.first_sync_started`, `match.card_opened`, `match.applied`. Read with `count(col)` per stage to see drop-off.
- **`analytics_feature_usage_30d`** — `name`, occurrences, distinct sessions, distinct profiles for the trailing 30 days, ordered by occurrences. Tells you what's actually used.
- **`analytics_dismiss_patterns`** — group `match.dismissed` by `properties->>'source'` and `(properties->>'score')::numeric`. Surfaces "high-scoring dismissed matches" (matcher quality signal) and swipe-vs-kebab ratio (mobile-vs-desktop).
- **`analytics_cover_letter_funnel`** — per `application_id`, did the user click generate, succeed, edit, download, apply? Counts each stage.
- **`analytics_sync_friction_30d`** — daily counts of `feed.sync_clicked` / `_succeeded` / `_failed`. How often is manual sync needed; how often does it fail.

### Privacy

- No IP addresses logged.
- `user_agent` truncated to 512 chars; used only for device-class bucketing in views you'd add later.
- 90-day retention.
- Authenticated events tied to `profile_id`; anonymous to a random `session_id`.
- Portfolio context with effectively one user (the developer) plus occasional reviewers — no GDPR controller obligations triggered. Documented inline in `events.py`.

## Out of scope (deliberate)

- Light theme. Tokens are shaped to allow it later (single CSS variable layer to flip), but no QA/design budget spent on it now.
- Internationalization. English copy only.
- Application-tracker features beyond pending/applied/dismissed (interview stages, follow-up reminders, email parsing). Out of scope for this redesign.
- Push notifications / email digests for "new high-match found." Considered, deferred — not blocking the redesign.
- A/B testing infrastructure. The analytics layer is for observation, not experimentation.
- Re-architecting the chat agent itself. Only the frontend treatment of replies and the SSE meta marker change.
- "Open resume in new tab" backend endpoint, if one doesn't already exist. Settings page falls back to "Re-upload" only; the read-resume path is a small follow-up.
- Replacing TanStack Query, React Router, or Tailwind with anything else. Same stack, same deps (no new ones added except possibly a small swipe-gesture utility — and even that is optional; pointer events are sufficient).

## Testing strategy

- **Vitest unit tests** colocated with each new component in `src/components/ui/`. Snapshot-free; assertion-based (rendering, role, aria).
- **Playwright e2e** specs updated alongside route changes:
  - `auth-and-nav.spec.ts` — header surface (Settings / Coach / Sign out reachable).
  - `matching.spec.ts` — feed renders cards; status chip switch updates list; sync button triggers sync and shows live state; swipe + kebab both dismiss; undo toast restores.
  - `application-review.spec.ts` — match detail shows description inline (no expander); cover letter editor flow; "Open posting" marks applied.
  - `onboarding.spec.ts` — feed shows profile-completeness card in setup mode; coach drawer opens pre-prompted; first sync triggers card collapse.
- **Visual regression** is not introduced (no Chromatic / Loki). Covered by manual review during PRs.
- **Analytics tests**: `track.ts` unit tests for batching / flush behavior; one e2e that asserts events land in the DB after a known interaction (uses `--has-seed-api` flag pattern from existing smoke tests).

## Implementation notes / constraints

- Stack stays: React 18 + Vite + TypeScript + TanStack Query + React Router + Tailwind 3. No new runtime deps if avoidable.
- Tailwind config gets a small `extend.colors` block referencing the CSS variables, plus a `extend.borderRadius` and `extend.fontFamily`.
- `RequireAuth` and `AuthContext` unchanged.
- API client (`src/api/client.ts`) gets two additions: `track()` (in `lib/track.ts`, separate file) and `listApplicationCounts()` if we add a counts endpoint server-side; otherwise the chip counts come from the existing `listApplications` payloads.
- The match detail "Open posting → markApplied" change uses the existing `markApplied(id)` endpoint, called optimistically on click.
- Per CLAUDE.md, all new SQLModel models with ARRAY/JSONB fields use explicit `sa_column=Column(...)`; new model registered in `app/models/__init__.py`; migration created via `make migrate ARGS="revision --autogenerate -m '...'"` against local Postgres.

## Risks

- **Swipe gesture on web is fiddly.** Mitigation: pointer-events with a clear commit threshold, kebab fallback always present, `prefers-reduced-motion` disables. If touch-only behavior turns out flaky in QA, kebab becomes the sole interaction (no spec change needed — the fallback is the same UI).
- **Profile-completeness card going stale relative to actual profile state.** Mitigation: refetch on Coach close + on every `updateProfile` / `uploadResume` mutation success via a `profile` query invalidation. Acceptable lag is "next interaction."
- **Optimistic mark-applied diverging from reality** (user clicks Open, never applies). Mitigation: Move-back-to-pending is one tap away in the kebab; behavior is documented to the user via a small "Tap = considered applied" hint near the button on first encounter (dismissable; stored in localStorage).
- **Analytics table growth on a real user base.** Mitigation: 90-day retention via daily maintenance keeps the table bounded; even at 1000 events/user/day × 90 days × 100 users = 9M rows, well within a single Postgres table's comfort zone. Indexed selects stay sub-100ms.

## Success criteria

- No accidental dismissals reported in casual self-use after migration.
- Match card whole-card tap works on iOS Safari, Chrome Android, and desktop browsers.
- Job description is fully visible on detail without any tap to expand.
- Resume upload triggers a search without requiring navigation back to feed.
- Profile-completeness card disappears once the profile is healthy.
- `analytics_feature_usage_30d` returns non-trivial rows for every event in the canon — proves instrumentation is wired correctly.
- Lighthouse mobile score improves on `/` (current is "fine, not great"; target ≥ 90 across Performance / Accessibility / Best Practices).
