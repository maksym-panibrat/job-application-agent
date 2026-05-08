# Desktop apply CTA + Coach → Chat rename + Job Search rebrand + Undismiss

Date: 2026-05-07
Status: Approved (pending implementation)

## Context

Four frontend UX papercuts, bundled into one spec/PR because they all touch the same matches surface (`MatchCard`, `ApplicationReview`, `StickyActions`, `Toast`) and the same chrome (`AppShell`, `Landing`, `index.html`):

1. **Desktop apply requires three clicks.** On the `ApplicationReview` page, `StickyActions` (the bottom bar containing "Open posting ↗") is `md:hidden`, so on desktop the only way to "apply" is to click the header kebab `⋯` and select "Open original posting ↗".
2. **"Coach" is the wrong name for the chat modal.** The drawer is a chat UI, not a coaching feature. Rename to "Chat" — for the user-visible label, the URL param, and the telemetry events. Component, icon, and file names follow.
3. **Site title is stale.** The page title and brand currently say "Job Agent" / "Job Application Agent". The product's domain is "Job Search". Rebrand all chrome to "Job Search".
4. **No undo for dismiss.** Once a match is dismissed (swipe, mobile bar Skip, kebab Dismiss, or detail-page Skip), there's no way back. Misclicks — especially via swipe — are unrecoverable. Frontend silently treats `pending_review` as unsupported in `ApplicationReview.tsx:41`, but the backend (`app/api/applications.py:143`) actually accepts it and even clears `applied_at` for the undo path.

## Goals

- Desktop users can apply to a matched job in one click from the detail page.
- The chat modal is consistently named "Chat" everywhere — UI labels, files, URL param, telemetry.
- Site brand says "Job Search" everywhere — `<title>`, header brand link, landing hero.
- Dismiss is reversible **forever**. A dismissed match can be restored to pending at any later point — next week, next month — through the Dismissed-tab `MatchCard` kebab. (The `ApplicationReview` detail page is intentionally unchanged for dismissed apps; undismiss is exclusively a list-page action.) The undo toast is just an ergonomic shortcut for the immediate-misclick case; missing it never costs the user the ability to recover, because the durable Restore action has no time limit.

## Non-goals

- Adding a quick-apply shortcut on the `MatchCard` list itself. The list already routes to detail in one click; the friction is on the detail page.
- Mobile UX changes for apply. The mobile `StickyActions` bottom bar is the right pattern; leave it alone.
- Backend changes. The review endpoint already supports `pending_review`; "Coach" appears nowhere in the backend domain.
- Re-running matching for restored applications, or any side effect beyond status flip + clearing `applied_at`.

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

## Part 2 — Coach → Chat rename + Job Search rebrand

These two renames ship together since they touch the same chrome and would otherwise be one churned commit each.

### 2a. Coach → Chat

#### Surface area

| Surface                      | Before                                  | After                                |
|------------------------------|-----------------------------------------|--------------------------------------|
| Component dir                | `components/coach/`                     | `components/chat/`                   |
| Component                    | `Coach`                                 | `Chat`                               |
| Drawer component             | `CoachDrawer`                           | `ChatDrawer`                         |
| Icon                         | `ui/icons/Coach.tsx` (export `Coach`)   | `ui/icons/Chat.tsx` (export `Chat`)  |
| Drawer title (UI label)      | "Coach"                                 | "Chat"                               |
| IconButton aria-label        | "Coach"                                 | "Chat"                               |
| Settings CTA label           | "✦ Open Coach"                          | "✦ Open Chat"                        |
| ProfileCompletenessCard CTA  | "Tell coach →"                          | "Open chat →"                        |
| AppShell mobile-menu item    | "Coach"                                 | "Chat"                               |
| URL param key                | `coach`                                 | `chat`                               |
| Deep-link hrefs              | `?coach=1&prompt=…`                     | `?chat=1&prompt=…`                   |
| Telemetry event prefix       | `coach.*`                               | `chat.*`                             |

The "Tell coach →" label is reworded to "Open chat →" because "Tell chat" reads awkwardly — chat is a destination, not a recipient. The behaviour and prompt slug are unchanged; only the label rewords.

#### Telemetry events to rename

All in `frontend/src/components/coach/Coach.tsx` and `CoachDrawer.tsx`:
- `coach.opened` → `chat.opened`
- `coach.message_sent` → `chat.message_sent`
- `coach.message_failed` → `chat.message_failed`
- `coach.search_now_clicked` → `chat.search_now_clicked`

User confirmed the rename is acceptable despite the metric-continuity break.

#### Files touched (concrete list)

```
RENAMED (git mv)
  frontend/src/components/coach/Coach.tsx              → components/chat/Chat.tsx
  frontend/src/components/coach/Coach.test.tsx         → components/chat/Chat.test.tsx
  frontend/src/components/coach/CoachDrawer.tsx        → components/chat/ChatDrawer.tsx
  frontend/src/components/coach/CoachDrawer.test.tsx   → components/chat/ChatDrawer.test.tsx
  frontend/src/components/ui/icons/Coach.tsx           → components/ui/icons/Chat.tsx

EDITED
  frontend/src/components/chat/Chat.tsx                (export Chat, telemetry rename)
  frontend/src/components/chat/ChatDrawer.tsx          (export ChatDrawer, ?chat=1, title "Chat", telemetry)
  frontend/src/components/ui/icons/Chat.tsx            (export Chat)
  frontend/src/components/ui/icons/index.ts            (export { Chat } from './Chat')
  frontend/src/App.tsx                                 (import { ChatDrawer } from './components/chat/ChatDrawer')
  frontend/src/components/AppShell.tsx                 (icon, openChat, aria-label "Chat", URL param "chat", menu item "Chat")
  frontend/src/components/AppShell.test.tsx            (assertions: getByRole button name /chat/i, dialog name "Chat")
  frontend/src/components/settings/ProfileSummary.tsx  (link to "?chat=1&prompt=change_profile", label "✦ Open Chat")
  frontend/src/components/settings/ProfileSummary.test.tsx (assertions /open chat/i, /chat=1/)
  frontend/src/components/feed/ProfileCompletenessCard.tsx (CTA copy "Open chat →")
  frontend/src/components/feed/ProfileCompletenessCard.test.tsx (assertion updated)
  frontend/src/components/ui/Drawer.test.tsx           (test fixture title — pick a neutral string like "Drawer test" so we don't entangle it with the chat rename)
```

#### Verification of the rename

1. After all edits, `rg -wi 'coach' frontend/src` must return zero hits. The grep is the source of truth — if it returns anything, fix it. (Word-boundary form `-w` so `coach` stays caught but unrelated substrings don't false-match — there shouldn't be any either way.)
2. `cd frontend && npm run typecheck` (or `tsc --noEmit`) must pass.
3. `cd frontend && npm test` must pass.
4. `cd frontend && npm run lint` must pass.
5. Run `npm run dev`, open the app, click the chat icon in the header, confirm the drawer opens and the URL becomes `?chat=1`, send a message, confirm telemetry events emit `chat.message_sent`. Capture screenshots for the PR.

#### Risks

- **External deep links to `?coach=1`** would break. None known in the repo. If a user has bookmarked one, it silently won't open the drawer — acceptable.
- **Backend-rendered `coach=1` URLs.** Grep before merging:
  ```
  rg -n 'coach=1' app/ alembic/ scripts/
  ```
  Expected: zero hits (Coach is a frontend-only term). Fix if not.

### 2b. "Job Application Agent" / "Job Agent" → "Job Search"

| Surface                                | Before                       | After          |
|----------------------------------------|------------------------------|----------------|
| `frontend/index.html` `<title>`        | "Job Application Agent"      | "Job Search"   |
| `AppShell.tsx:32` brand link           | "Job Agent"                  | "Job Search"   |
| `Landing.tsx:59` hero `<h1>`           | "Job Application Agent"      | "Job Search"   |
| `AppShell.test.tsx:90` brand assertion | `getByText('Job Agent')`     | `getByText('Job Search')` |
| `e2e/auth-and-nav.spec.ts:29`          | heading `/Job Application Agent/i` | `/Job Search/i` |
| `e2e/auth-and-nav.spec.ts:133`         | link `'Job Agent'`           | `'Job Search'` |

**Out of scope.** Repository name (`job-application-agent`), backend module names (`app/agents/…`), and the GitHub README — these are dev-facing and the rebrand is product-facing only. If the user wants those renamed too, that's a separate change.

#### Verification

`rg 'Job (Agent|Application Agent)' frontend/` returns zero hits after the change.

---

## Part 3 — Undismiss

### Two complementary paths, no time limit on recovery

Dismiss must be reversible at **any point in the future** — there is no expiring window. Two paths surface the same `reviewApplication(id, 'pending_review')` action, layered for ergonomics:

- **Durable path (primary, list-only).** A "Restore" action in the `MatchCard` kebab when viewing the Dismissed tab (`?status=dismissed`). This is the canonical undismiss. It has no TTL; it works next week, next month, indefinitely. The `ApplicationReview` detail page is **not** modified for dismissed apps — to undismiss, the user opens the Dismissed tab and clicks Restore on the card. The detail page stays scoped to "review and act on a pending match".
- **Toast shortcut (secondary).** Right after a fresh dismiss, an "Undo" button on the success toast lets the user reverse the action without navigating to the Dismissed tab. The toast disappears after 8 seconds — but missing it has zero impact on recoverability because the durable path remains. The toast is only a convenience; it is **not** the undismiss feature.

### Layer 3a — Undo toast on dismiss (convenience shortcut)

After every successful `dismiss.mutate()`, the toast that currently says "Dismissed {title}" gets an "Undo" action. Clicking it calls `reviewApplication(id, 'pending_review')`, which the backend already supports and which clears `applied_at` server-side (`applications.py:143-145`).

Toast TTL is **8 seconds** for actionable toasts (current default 5s is too tight to react to a swipe-dismiss). Non-actionable toasts keep the 5s default. The TTL on the toast does not constrain the undismiss feature itself — it only governs how long the convenience shortcut stays on screen.

**Toast component extension.** `Toast.tsx` currently has no action support. Extend the API:

```ts
interface ToastAction { label: string; onClick: () => void }
interface ToastContextValue {
  show: (
    message: string,
    intent?: ToastIntent,
    options?: { action?: ToastAction; ttlMs?: number }
  ) => void
}
```

The action renders as a button on the right side of the toast (e.g., `<button class="ml-3 underline font-semibold">Undo</button>`). Clicking the action dismisses the toast and runs `onClick`. Default behaviour (no `action`, no `ttlMs`) is unchanged — every existing call site keeps working.

Telemetry on undo: `track('match.undismissed', { application_id, source: 'toast' })`.

### Layer 3b — Restore on the dismissed match (Dismissed tab only)

**Scope.** This change is confined to the `MatchCard` rendered inside the Dismissed tab. The `ApplicationReview` detail page is **not** modified for dismissed apps — its kebab keeps only "Open original posting ↗" (the existing item). Users undismiss from the list, not from the detail page.

**On `MatchCard` (Dismissed tab).** When `app.status === 'dismissed'`, the kebab's "Dismiss" item is replaced with "Restore". Clicking it calls `reviewApplication(id, 'pending_review')`. Other items (Save for later, Open original posting ↗) stay.

`SwipeableCard` behaviour on dismissed cards: keep swipe-to-dismiss disabled-or-no-op for `dismissed` cards, since "dismiss again" is meaningless. Easiest path: pass a different `onCommit` (or `undefined`) when status is `dismissed` and let `SwipeableCard` no-op. Verify by reading the component before deciding the exact wiring; if the simplest path is to keep swipe enabled but make it a no-op, that's fine.

Telemetry: `track('match.undismissed', { application_id, source: 'kebab' })`.

### Stale comment / type cast cleanup

`ApplicationReview.tsx:41-44` has:
- A comment claiming the backend rejects `pending_review` (false — confirmed in `applications.py:136`).
- A cast `'pending_review' as 'dismissed' | 'applied'` to bypass the client signature.

Fix:
- Remove the comment.
- Widen the client type: `reviewApplication: (id: string, status: 'dismissed' | 'applied' | 'pending_review') => …` in `frontend/src/api/client.ts:176`.
- Drop the cast.

### Tests

- `Toast.test.tsx`: clicking the action button runs `onClick` and dismisses the toast. Custom `ttlMs` is honoured. Toasts without an action still render (regression).
- `MatchCard.test.tsx`: a card with `status='dismissed'` shows "Restore" instead of "Dismiss" in the kebab; clicking it POSTs `pending_review` and refreshes the list.
- `MatchCard.test.tsx`: dismissing a `pending_review` card produces a toast with an "Undo" button; clicking Undo POSTs `pending_review`.
- `client.ts` typing: existing usages compile after the type widens to include `'pending_review'`; no `as` cast remains.

### Risks / accepted gaps

- **Race condition.** If the user clicks "Undo" while the dismiss request is still in flight, two PATCHes race. Acceptable: both write to the same row; whichever lands last wins, and the eventual state is the user's intent (`pending_review`). Add no client-side gate for this — the backend serialises.
- **Toast covers content.** With an action button, the toast is wider. Mobile screens are narrow. Verify visually in the dev smoke; if it overflows, drop `max-w-xs` and let it size to content.
- **No toast undo for `dismissed` arriving via API failure.** If `dismiss.mutate()` errors, no toast (current behaviour shows error toast instead) — no undo needed because the dismiss didn't happen.

---

## Plan structure (for the executor)

The implementation plan treats these as three independent task tracks — parallelisable for review, single PR for delivery:

- **Track A — Desktop apply CTA**
  - A1: Extract `useApplyAction` hook + unit tests.
  - A2: Add `HeaderApplyButton` component + unit tests.
  - A3: Wire into `ApplicationReview` header + adjust page-level test.
  - A4: Refactor `StickyActions` to consume the hook (preserve test pass).

- **Track B — Coach → Chat rename + Job Search rebrand**
  - B1: `git mv` the five files (`coach/*`, `ui/icons/Coach.tsx`); update imports so the app still builds.
  - B2: Replace user-visible "Coach" labels and URL param to `chat` (rewording "Tell coach →" to "Open chat →").
  - B3: Rename telemetry events `coach.*` → `chat.*`.
  - B4: Replace "Job Agent" / "Job Application Agent" with "Job Search" in `index.html`, `AppShell.tsx`, `Landing.tsx`, and the matching test/e2e assertions.
  - B5: Run `rg -wi coach frontend/src` and `rg 'Job (Agent|Application Agent)' frontend/` — both must return zero hits.

- **Track C — Undismiss**
  - C1: Widen `reviewApplication` client signature; remove stale comment + cast in `ApplicationReview.tsx`.
  - C2: Extend `Toast.tsx` to accept an optional `action` and `ttlMs`; add tests.
  - C3: Wire undo-toast into every `dismiss.mutate()` success path (`MatchCard.tsx`, `ApplicationReview.tsx`, `StickyActions.tsx`).
  - C4: Add "Restore" kebab item on dismissed `MatchCard` (replaces "Dismiss"); make swipe a no-op on dismissed cards.
  - **Out of scope for this track:** the `ApplicationReview` detail-page kebab is not extended for dismissed apps. Undismiss lives on the Dismissed list only.

- **Track D — Verification & PR**
  - Type-check, lint, tests, manual dev smoke.
  - PR with desktop screenshots: (1) `ApplicationReview` desktop showing the new "Open posting ↗" header CTA, (2) the header showing the "Chat" icon button, (3) the drawer titled "Chat", (4) the dismiss undo toast (action button visible), (5) the Dismissed-tab kebab showing "Restore", (6) the rebranded "Job Search" header brand. Per user preference, real screenshots only — no ASCII mockups.

## Acceptance criteria

- On desktop (≥`md`), the `ApplicationReview` page shows a visible "Open posting ↗" primary button in the header. Clicking it opens the URL in a new tab and (for `pending_review`) marks the application applied. (Tested.)
- On mobile, the existing bottom sticky bar is unchanged.
- `rg -wi 'coach' frontend/src` returns zero hits.
- `rg 'Job (Agent|Application Agent)' frontend/` returns zero hits; the `<title>`, header brand link, and landing hero all read "Job Search".
- A dismissed match can be restored to `pending_review` at any point in the future — there is no time limit. The Dismissed-tab (`?status=dismissed`) `MatchCard` kebab shows "Restore"; clicking it POSTs the transition and the card moves back to the Pending tab. (Tested.)
- The `ApplicationReview` detail page is **unchanged** for dismissed apps. Undismiss is exclusively a list-page action.
- As a convenience, dismissing a match anywhere in the app (swipe, kebab, mobile sticky Skip, detail kebab) also shows a toast with an "Undo" button for 8 seconds. Clicking it within that window restores the match. The toast's TTL is purely cosmetic — it does not bound the user's ability to undo, because the durable Restore action above remains available indefinitely. (Tested.)
- Stale comment + type cast in `ApplicationReview.tsx` removed; `reviewApplication` client signature includes `'pending_review'`. (Even though the detail-page kebab does not gain a Restore item, the client widening is still required for the toast's Undo action and the list-card Restore item.)
- All frontend tests, type-check, and lint pass.
- A single PR contains all four tracks with screenshots and a note in the body that the rename includes telemetry events (`coach.*` → `chat.*`) and the URL param (`?coach=1` → `?chat=1`), plus the "Job Search" rebrand.
