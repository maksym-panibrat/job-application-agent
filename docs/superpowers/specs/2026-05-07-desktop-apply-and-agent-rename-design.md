# Desktop apply CTA + Coach → Agent rename

Date: 2026-05-07
Status: Approved (pending implementation)

## Context

Two unrelated frontend papercuts surfaced together:

1. **Desktop apply requires three clicks.** On the `ApplicationReview` page, `StickyActions` (the bottom bar containing "Open posting ↗") is `md:hidden`, so on desktop the only way to "apply" is to click the header kebab `⋯` and select "Open original posting ↗". The user's complaint: "too many clicks".
2. **"Coach" naming is inconsistent.** The brand is "Job Agent" / "Job Application Agent" (see `AppShell.tsx:32`, `Landing.tsx:59`). The drawer/component/icon/URL-param/event-names all still say "Coach".

This spec lands both as a single change set since they share a thin slice of the frontend (header chrome + drawer entry) and one PR keeps churn minimal.

## Goals

- Desktop users can apply to a matched job in one click from the detail page.
- Naming is consistent: every user-visible "Coach" surface and every internal coach-named symbol (files, props, URL param, telemetry events) becomes "Agent".

## Non-goals

- Adding a quick-apply shortcut on the `MatchCard` list itself. The list already routes to detail in one click; the friction is on the detail page.
- Mobile UX changes. The mobile `StickyActions` bottom bar is the right pattern; leave it alone.
- Backend changes. "Coach" appears nowhere in the backend domain — agent module names already use `agent`.

---

## Part 1 — Desktop apply CTA

### Change shape

Add a primary "Open posting ↗" button to the desktop header on `ApplicationReview`, hidden on mobile.

```
desktop:  [← Back]                       [Open posting ↗]  [⋯]
mobile:   [← Back]                                          [⋯]
          (existing mobile sticky bottom bar unchanged)
```

### Behaviour matrix

| `app.status`       | Desktop header CTA          | onClick semantics                                                                |
|--------------------|-----------------------------|----------------------------------------------------------------------------------|
| `pending_review`   | "Open posting ↗" (accent)   | `window.open(applyUrl, '_blank', 'noopener')` + `markApplied.mutate()`           |
| `applied`          | "Open posting again ↗"      | `window.open(applyUrl, '_blank', 'noopener')` (no second mutate)                 |
| `dismissed`        | not rendered                | (kebab still has actions)                                                        |

Telemetry mirrors `StickyActions.onOpenAndMark`:
- Always `track('match.original_posting_opened', { application_id })`.
- On the pending → applied transition, also `track('match.applied', { application_id })`.

### Implementation outline

1. Extract the open-and-mark logic into a small hook so `StickyActions` and the new desktop button share it:

   ```tsx
   // frontend/src/components/match-detail/useApplyAction.ts (new)
   export function useApplyAction(args: { appId, status, applyUrl }) {
     // returns { onOpen, isApplied, label, markApplied (mutation) }
   }
   ```

   Hook centralises the `window.open` + conditional `markApplied` logic. Both `StickyActions` and the new desktop CTA call it.

2. New file `frontend/src/components/match-detail/HeaderApplyButton.tsx` — renders the desktop-only CTA. Class names: `hidden md:inline-flex` plus the same accent button styling used in `StickyActions`. Status `dismissed` → `null`.

3. Wire it into the header in `pages/ApplicationReview.tsx`:

   ```tsx
   <header className="...flex items-center justify-between">
     <IconButton aria-label="Back" onClick={…} />
     <div className="flex items-center gap-2">
       <HeaderApplyButton appId={app.id} status={app.status} applyUrl={app.job.apply_url} />
       <IconButton aria-label="More actions" onClick={…} />
     </div>
   </header>
   ```

4. Remove "Open original posting ↗" from the desktop kebab. Approach: gate that `ActionSheetItem` on a `useMediaQuery`-style hook, OR — simpler — keep it in the kebab on all viewports for now (the desktop CTA renders the kebab item redundant but harmless). **Pick the simpler path: leave the kebab item in place.** Rationale: introducing a media-query branch in the kebab adds a new responsive primitive for a marginal benefit; redundancy is fine.

5. Refactor `StickyActions` to use the new `useApplyAction` hook, preserving its current behaviour exactly.

### Tests (Vitest + React Testing Library)

- `HeaderApplyButton.test.tsx`:
  - Renders the button with status `pending_review`; clicking it opens URL and POSTs mark-applied. (Mirror of `StickyActions.test.tsx:34`.)
  - Renders "Open posting again ↗" when status is `applied`; clicking opens URL but does NOT POST mark-applied.
  - Renders nothing when status is `dismissed`.
- `ApplicationReview.test.tsx`: assert the header CTA is present on the page (presence test only — the button-level cases live in the unit file).
- Existing `StickyActions.test.tsx`: should still pass after the hook extraction. If anything breaks, fix the test (refactor invariant).

### Out of scope / accepted gaps

- The desktop CTA is duplicated logic with the kebab "Open original posting" item. Acceptable.
- No new e2e tests; the unit-level coverage is enough.

---

## Part 2 — Coach → Agent rename

### Surface area

| Surface                      | Before                             | After                              |
|------------------------------|------------------------------------|------------------------------------|
| Component dir                | `components/coach/`                | `components/agent/`                |
| Component                    | `Coach`                            | `Agent`                            |
| Drawer component             | `CoachDrawer`                      | `AgentDrawer`                      |
| Icon                         | `ui/icons/Coach.tsx` (export `Coach`) | `ui/icons/Agent.tsx` (export `Agent`) |
| Drawer title (UI label)      | "Coach"                            | "Agent"                            |
| IconButton aria-label        | "Coach"                            | "Agent"                            |
| Settings CTA label           | "✦ Open Coach"                     | "✦ Open Agent"                     |
| ProfileCompletenessCard CTA  | "Tell coach →"                     | "Tell agent →"                     |
| URL param key                | `coach`                            | `agent`                            |
| Deep-link hrefs              | `?coach=1&prompt=…`                | `?agent=1&prompt=…`                |
| Telemetry event prefix       | `coach.*`                          | `agent.*`                          |
| Test names / fixtures        | references to "coach"              | references to "agent"              |

### Telemetry events to rename

All in `frontend/src/components/coach/Coach.tsx` and `CoachDrawer.tsx`:
- `coach.opened` → `agent.opened`
- `coach.message_sent` → `agent.message_sent`
- `coach.message_failed` → `agent.message_failed`
- `coach.search_now_clicked` → `agent.search_now_clicked`

User confirmed the rename is acceptable despite the metric-continuity break.

### Files touched (concrete list)

```
RENAMED (git mv)
  frontend/src/components/coach/Coach.tsx              → components/agent/Agent.tsx
  frontend/src/components/coach/Coach.test.tsx         → components/agent/Agent.test.tsx
  frontend/src/components/coach/CoachDrawer.tsx        → components/agent/AgentDrawer.tsx
  frontend/src/components/coach/CoachDrawer.test.tsx   → components/agent/AgentDrawer.test.tsx
  frontend/src/components/ui/icons/Coach.tsx           → components/ui/icons/Agent.tsx

EDITED
  frontend/src/components/agent/Agent.tsx              (export Agent, telemetry rename)
  frontend/src/components/agent/AgentDrawer.tsx        (export AgentDrawer, ?agent=1, title "Agent", telemetry)
  frontend/src/components/ui/icons/Agent.tsx           (export Agent)
  frontend/src/components/ui/icons/index.ts            (export { Agent } from './Agent')
  frontend/src/App.tsx                                 (import { AgentDrawer } from './components/agent/AgentDrawer')
  frontend/src/components/AppShell.tsx                 (icon, openAgent, aria-label "Agent", URL param "agent", menu item "Agent")
  frontend/src/components/AppShell.test.tsx            (assertions: getByRole button name /agent/i, dialog name "Agent")
  frontend/src/components/settings/ProfileSummary.tsx  (link to "?agent=1&prompt=change_profile", label "✦ Open Agent")
  frontend/src/components/settings/ProfileSummary.test.tsx (assertions /open agent/i, /agent=1/)
  frontend/src/components/feed/ProfileCompletenessCard.tsx (CTA copy "Tell agent")
  frontend/src/components/feed/ProfileCompletenessCard.test.tsx (assertion "Tell agent")
  frontend/src/components/ui/Drawer.test.tsx           (test fixture title "Agent" — purely a string change in the test)
```

### Verification of the rename

1. After all edits, `rg -i 'coach' frontend/src` must return zero hits except (a) inside CHANGELOG/git history files (none here) and (b) any intentional residue (none planned). The grep is the source of truth — if it returns anything, fix it.
2. `cd frontend && npm run typecheck` (or `tsc --noEmit`) must pass.
3. `cd frontend && npm test` must pass.
4. `cd frontend && npm run lint` must pass.
5. Run `npm run dev`, open the app, click the agent icon in the header, confirm the drawer opens and the URL becomes `?agent=1`, send a message, confirm telemetry network calls (or console-logged events in dev) emit `agent.message_sent`. Capture screenshots for the PR per the user's standing preference (frontend PRs include real screenshots).

### Risks

- **External deep links to `?coach=1`** would break. None known in the repo; assume none in the wild. If a user has bookmarked one, it silently won't open the drawer — acceptable.
- **Bookmarked `coach=1` deep links from emails/onboarding flows.** Grep `app/` for any backend-rendered URL containing `coach=1` before merging:
  ```
  rg -n 'coach=1' app/ alembic/ scripts/
  ```
  If hits, update or coordinate. (Expected: zero hits, since "Coach" is a frontend-only term.)

---

## Plan structure (for the executor)

The implementation plan should treat these as two independent task tracks (parallelisable for review, but a single PR):

- **Track A — Desktop apply CTA**
  - A1: Extract `useApplyAction` hook + unit tests.
  - A2: Add `HeaderApplyButton` component + unit tests.
  - A3: Wire into `ApplicationReview` header + adjust page-level test.
  - A4: Refactor `StickyActions` to consume the hook (preserve test pass).

- **Track B — Coach → Agent rename**
  - B1: `git mv` the five files; update imports so the app still builds.
  - B2: Replace all user-visible "Coach" labels and URL param to `agent`.
  - B3: Rename telemetry events `coach.*` → `agent.*`.
  - B4: Update all tests; run `rg -i coach frontend/src` to verify zero hits.

- **Track C — Verification & PR**
  - Type-check, lint, tests, manual dev smoke.
  - PR with desktop screenshots: (1) `ApplicationReview` showing the new "Open posting ↗" button, (2) the header showing "Agent" icon button, (3) the drawer titled "Agent". Per user preference, real screenshots only — no ASCII mockups.

## Acceptance criteria

- On desktop (≥`md`), the `ApplicationReview` page shows a visible "Open posting ↗" primary button in the header. Clicking it opens the URL in a new tab and (for `pending_review`) marks the application applied. (Tested.)
- On mobile, the existing bottom sticky bar is unchanged.
- `rg -i 'coach' frontend/src` returns zero hits.
- All frontend tests, type-check, and lint pass.
- A single PR contains both changes with screenshots and a note in the body that the rename includes telemetry events (`coach.*` → `agent.*`) and the URL param (`?coach=1` → `?agent=1`).
