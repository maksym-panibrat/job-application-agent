# Matches UX Papercuts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land four scoped frontend UX fixes in one PR — desktop apply CTA on the match detail page, Coach→Chat rename, Job Search rebrand, and durable kebab-based undismiss.

**Architecture:** All work is in `frontend/`. No backend changes. Track A extracts a small hook so `StickyActions` (mobile) and a new `HeaderApplyButton` (desktop) share the open-and-mark logic. Track B is a mechanical rename + rebrand verified by `rg`. Track C wires the existing `pending_review` backend path (already supported) into two kebab items.

**Tech Stack:** React 18 + TypeScript, Vite, Vitest + React Testing Library, MSW for API mocks, Tailwind, react-router-dom, @tanstack/react-query.

**Spec:** `docs/superpowers/specs/2026-05-07-matches-ux-papercuts-design.md`.

---

## File map

### Created
- `frontend/src/components/match-detail/useApplyAction.ts` — shared open-and-mark logic.
- `frontend/src/components/match-detail/useApplyAction.test.ts` — hook tests.
- `frontend/src/components/match-detail/HeaderApplyButton.tsx` — desktop-only header CTA.
- `frontend/src/components/match-detail/HeaderApplyButton.test.tsx` — component tests.
- `frontend/src/components/chat/Chat.tsx` — renamed from `coach/Coach.tsx`.
- `frontend/src/components/chat/Chat.test.tsx` — renamed from `coach/Coach.test.tsx`.
- `frontend/src/components/chat/ChatDrawer.tsx` — renamed from `coach/CoachDrawer.tsx`.
- `frontend/src/components/chat/ChatDrawer.test.tsx` — renamed from `coach/CoachDrawer.test.tsx`.
- `frontend/src/components/ui/icons/Chat.tsx` — renamed from `Coach.tsx`.

### Modified
- `frontend/src/components/match-detail/StickyActions.tsx` — consume `useApplyAction`.
- `frontend/src/pages/ApplicationReview.tsx` — header CTA, drop stale comment + cast, extend kebab gate to `dismissed`.
- `frontend/src/pages/ApplicationReview.test.tsx` — header CTA presence, kebab Restore on dismissed.
- `frontend/src/api/client.ts` — widen `reviewApplication` signature.
- `frontend/src/components/feed/MatchCard.tsx` — Restore on dismissed, swipe no-op on dismissed.
- `frontend/src/components/feed/MatchCard.test.tsx` — Restore tests.
- `frontend/src/components/AppShell.tsx` — Chat icon/button/URL param/menu item, "Job Search" brand.
- `frontend/src/components/AppShell.test.tsx` — assertions for Chat + Job Search.
- `frontend/src/components/settings/ProfileSummary.tsx` — `?chat=1`, "Open Chat" label.
- `frontend/src/components/settings/ProfileSummary.test.tsx` — assertion updates.
- `frontend/src/components/feed/ProfileCompletenessCard.tsx` — `?chat=1`, "Open chat →".
- `frontend/src/components/feed/ProfileCompletenessCard.test.tsx` — assertion updates.
- `frontend/src/components/ui/icons/index.ts` — export `Chat`.
- `frontend/src/App.tsx` — import `ChatDrawer`.
- `frontend/src/components/ui/Drawer.test.tsx` — neutral fixture title.
- `frontend/src/pages/Landing.tsx` — "Job Search" hero.
- `frontend/index.html` — `<title>Job Search</title>`.
- `frontend/e2e/auth-and-nav.spec.ts` — assertions for Job Search.

---

# Track A — Desktop apply CTA

### Task A1: Extract `useApplyAction` hook

**Files:**
- Create: `frontend/src/components/match-detail/useApplyAction.ts`
- Create: `frontend/src/components/match-detail/useApplyAction.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/match-detail/useApplyAction.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import type { ReactNode } from 'react'
import { useApplyAction } from './useApplyAction'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('useApplyAction', () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('onOpen opens URL in a new tab and POSTs mark-applied when status is pending_review', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied', applied_at: new Date().toISOString() })
      }),
    )
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'pending_review', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    await act(async () => { result.current.onOpen() })
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await waitFor(() => expect(posted).toBe(true))
  })

  it('onOpen opens URL but does NOT POST mark-applied when status is applied', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied' })
      }),
    )
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'applied', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    await act(async () => { result.current.onOpen() })
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    // Give the mutation a tick — should not fire.
    await new Promise((r) => setTimeout(r, 20))
    expect(posted).toBe(false)
  })

  it('exposes isApplied=true when status is applied', () => {
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'applied', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    expect(result.current.isApplied).toBe(true)
  })

  it('exposes isApplied=false when status is pending_review', () => {
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'pending_review', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    expect(result.current.isApplied).toBe(false)
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/match-detail/useApplyAction.test.ts`
Expected: FAIL — module `./useApplyAction` not found.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/components/match-detail/useApplyAction.ts`:

```ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'

export interface UseApplyActionArgs {
  appId: string
  status: string
  applyUrl: string
}

export function useApplyAction({ appId, status, applyUrl }: UseApplyActionArgs) {
  const qc = useQueryClient()
  const { show } = useToast()

  const markApplied = useMutation({
    mutationFn: () => api.markApplied(appId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', appId] }),
    onError: (e) => show((e as Error)?.message ?? "Couldn't mark as applied — try again", 'error'),
  })

  const isApplied = status === 'applied'

  function onOpen() {
    track('match.original_posting_opened', { application_id: appId })
    window.open(applyUrl, '_blank', 'noopener')
    if (status === 'pending_review') {
      track('match.applied', { application_id: appId })
      markApplied.mutate()
    }
  }

  return { onOpen, isApplied, markApplied }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/match-detail/useApplyAction.test.ts`
Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/useApplyAction.ts frontend/src/components/match-detail/useApplyAction.test.ts
git commit -m "$(cat <<'EOF'
feat(match-detail): extract useApplyAction hook

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A2: Add `HeaderApplyButton` component

**Files:**
- Create: `frontend/src/components/match-detail/HeaderApplyButton.tsx`
- Create: `frontend/src/components/match-detail/HeaderApplyButton.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/match-detail/HeaderApplyButton.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { HeaderApplyButton } from './HeaderApplyButton'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('HeaderApplyButton', () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('renders nothing when status is dismissed', () => {
    const { container } = render(withCtx(
      <HeaderApplyButton appId="a1" status="dismissed" applyUrl="https://x.com/" />
    ))
    expect(container).toBeEmptyDOMElement()
  })

  it('renders "Open posting ↗" when status is pending_review', () => {
    render(withCtx(
      <HeaderApplyButton appId="a1" status="pending_review" applyUrl="https://x.com/" />
    ))
    expect(screen.getByRole('link', { name: /open posting/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /open posting again/i })).not.toBeInTheDocument()
  })

  it('renders "Open posting again ↗" when status is applied', () => {
    render(withCtx(
      <HeaderApplyButton appId="a1" status="applied" applyUrl="https://x.com/" />
    ))
    expect(screen.getByRole('link', { name: /open posting again/i })).toBeInTheDocument()
  })

  it('clicking the button opens the URL and POSTs mark-applied when pending_review', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied', applied_at: new Date().toISOString() })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(
      <HeaderApplyButton appId="a1" status="pending_review" applyUrl="https://x.com/" />
    ))
    await user.click(screen.getByRole('link', { name: /open posting/i }))
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await waitFor(() => expect(posted).toBe(true))
  })

  it('clicking the button opens the URL but does NOT POST mark-applied when applied', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied' })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(
      <HeaderApplyButton appId="a1" status="applied" applyUrl="https://x.com/" />
    ))
    await user.click(screen.getByRole('link', { name: /open posting again/i }))
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await new Promise((r) => setTimeout(r, 20))
    expect(posted).toBe(false)
  })

  it('is hidden on mobile via the hidden md:inline-flex utility', () => {
    render(withCtx(
      <HeaderApplyButton appId="a1" status="pending_review" applyUrl="https://x.com/" />
    ))
    const link = screen.getByRole('link', { name: /open posting/i })
    expect(link.className).toContain('hidden')
    expect(link.className).toContain('md:inline-flex')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/components/match-detail/HeaderApplyButton.test.tsx`
Expected: FAIL — module `./HeaderApplyButton` not found.

- [ ] **Step 3: Implement the component**

Create `frontend/src/components/match-detail/HeaderApplyButton.tsx`:

```tsx
import { useApplyAction } from './useApplyAction'

export interface HeaderApplyButtonProps {
  appId: string
  status: string
  applyUrl: string
}

export function HeaderApplyButton({ appId, status, applyUrl }: HeaderApplyButtonProps) {
  const { onOpen, isApplied } = useApplyAction({ appId, status, applyUrl })

  if (status === 'dismissed') return null

  const label = isApplied ? 'Open posting again ↗' : 'Open posting ↗'
  const intentClass = isApplied
    ? 'bg-success/10 text-success border border-success/30'
    : 'bg-accent text-accent-fg'

  return (
    <a
      href={applyUrl}
      onClick={(e) => { e.preventDefault(); onOpen() }}
      className={`hidden md:inline-flex items-center justify-center font-semibold rounded-md-token px-3 py-1.5 text-sm min-h-[36px] ${intentClass}`}
    >
      {label}
    </a>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/match-detail/HeaderApplyButton.test.tsx`
Expected: PASS — all 6 tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/HeaderApplyButton.tsx frontend/src/components/match-detail/HeaderApplyButton.test.tsx
git commit -m "$(cat <<'EOF'
feat(match-detail): add HeaderApplyButton (desktop CTA)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A3: Wire `HeaderApplyButton` into `ApplicationReview`

**Files:**
- Modify: `frontend/src/pages/ApplicationReview.tsx`
- Modify: `frontend/src/pages/ApplicationReview.test.tsx`

- [ ] **Step 1: Add the failing page-level test**

Add this test to `frontend/src/pages/ApplicationReview.test.tsx` inside the `describe('Match detail (ApplicationReview)')` block, after the existing tests:

```tsx
it('renders the desktop HeaderApplyButton when status is pending_review', async () => {
  renderAt('/matches/a1', detail({ status: 'pending_review' }))
  await waitFor(() => expect(screen.getByRole('heading', { name: /senior backend engineer/i })).toBeInTheDocument())
  expect(screen.getByRole('link', { name: /open posting/i })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/ApplicationReview.test.tsx -t "renders the desktop HeaderApplyButton"`
Expected: FAIL — no link with name "Open posting" in the rendered tree.

- [ ] **Step 3: Wire the button into the header**

Modify `frontend/src/pages/ApplicationReview.tsx`. Replace the `<header>` block (currently at lines 61-68) with:

```tsx
<header className="sticky top-14 z-10 -mx-4 px-4 py-2 bg-bg/90 backdrop-blur border-b border-border flex items-center justify-between">
  <IconButton aria-label="Back" onClick={() => navigate(-1)}>
    <Close className="w-4 h-4" />
  </IconButton>
  <div className="flex items-center gap-2">
    <HeaderApplyButton appId={app.id} status={app.status} applyUrl={app.job.apply_url} />
    <IconButton aria-label="More actions" onClick={() => setMenuOpen(true)}>
      <Kebab className="w-4 h-4" />
    </IconButton>
  </div>
</header>
```

Add the import at the top (alongside the other component imports):

```tsx
import { HeaderApplyButton } from '../components/match-detail/HeaderApplyButton'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/ApplicationReview.test.tsx`
Expected: PASS — all tests green, including the new one.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ApplicationReview.tsx frontend/src/pages/ApplicationReview.test.tsx
git commit -m "$(cat <<'EOF'
feat(match-detail): show desktop apply CTA in ApplicationReview header

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task A4: Refactor `StickyActions` to consume `useApplyAction`

**Files:**
- Modify: `frontend/src/components/match-detail/StickyActions.tsx`

- [ ] **Step 1: Sanity-run existing tests (still green before refactor)**

Run: `cd frontend && npx vitest run src/components/match-detail/StickyActions.test.tsx`
Expected: PASS — all 4 existing tests pass.

- [ ] **Step 2: Refactor the component to consume the shared hook**

Replace the body of `frontend/src/components/match-detail/StickyActions.tsx` with:

```tsx
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'
import { track } from '../../lib/track'
import { useApplyAction } from './useApplyAction'

export interface StickyActionsProps {
  appId: string
  status: string
  applyUrl: string
}

export function StickyActions({ appId, status, applyUrl }: StickyActionsProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const { onOpen, isApplied } = useApplyAction({ appId, status, applyUrl })

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(appId, 'dismissed'),
    onSuccess: () => {
      show('Dismissed', 'info')
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  if (isApplied) {
    return (
      <div className="md:hidden fixed bottom-0 inset-x-0 bg-success/10 border-t border-success/30 px-4 py-3 flex items-center justify-between">
        <span className="text-sm text-success font-semibold">✓ Applied</span>
        <a
          href={applyUrl}
          onClick={(e) => { e.preventDefault(); onOpen() }}
          className="text-sm text-success underline"
        >
          Open posting again ↗
        </a>
      </div>
    )
  }

  return (
    <div className="md:hidden fixed bottom-0 inset-x-0 bg-surface border-t border-border p-3 flex gap-2 items-center"
         style={{ paddingBottom: 'calc(0.75rem + env(safe-area-inset-bottom, 0px))' }}>
      <Button
        size="md" variant="ghost"
        pending={dismiss.isPending}
        onClick={() => { track('match.dismissed', { application_id: appId, source: 'detail_skip' }); dismiss.mutate() }}
      >
        ⏷ Skip
      </Button>
      <a
        href={applyUrl}
        onClick={(e) => { e.preventDefault(); onOpen() }}
        className="flex-1 inline-flex items-center justify-center bg-accent text-accent-fg font-semibold rounded-md-token px-4 py-2.5 min-h-[40px]"
      >
        Open posting ↗
      </a>
    </div>
  )
}
```

- [ ] **Step 3: Run tests to verify still passing**

Run: `cd frontend && npx vitest run src/components/match-detail/StickyActions.test.tsx`
Expected: PASS — all 4 tests still green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/match-detail/StickyActions.tsx
git commit -m "$(cat <<'EOF'
refactor(match-detail): StickyActions consumes useApplyAction

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track B — Coach → Chat rename + Job Search rebrand

This track is a refactor. We will not write new failing tests for renames; instead we run the full frontend test suite as the gate. Each task ends with `npm test` passing.

### Task B1: `git mv` files (no edits)

**Files:**
- Rename: `frontend/src/components/coach/Coach.tsx` → `frontend/src/components/chat/Chat.tsx`
- Rename: `frontend/src/components/coach/Coach.test.tsx` → `frontend/src/components/chat/Chat.test.tsx`
- Rename: `frontend/src/components/coach/CoachDrawer.tsx` → `frontend/src/components/chat/ChatDrawer.tsx`
- Rename: `frontend/src/components/coach/CoachDrawer.test.tsx` → `frontend/src/components/chat/ChatDrawer.test.tsx`
- Rename: `frontend/src/components/ui/icons/Coach.tsx` → `frontend/src/components/ui/icons/Chat.tsx`

- [ ] **Step 1: Move the files**

Run from repo root:

```bash
mkdir -p frontend/src/components/chat
git mv frontend/src/components/coach/Coach.tsx frontend/src/components/chat/Chat.tsx
git mv frontend/src/components/coach/Coach.test.tsx frontend/src/components/chat/Chat.test.tsx
git mv frontend/src/components/coach/CoachDrawer.tsx frontend/src/components/chat/ChatDrawer.tsx
git mv frontend/src/components/coach/CoachDrawer.test.tsx frontend/src/components/chat/ChatDrawer.test.tsx
git mv frontend/src/components/ui/icons/Coach.tsx frontend/src/components/ui/icons/Chat.tsx
rmdir frontend/src/components/coach
```

- [ ] **Step 2: Verify the move is staged but build is broken**

Run: `git status -s` — expect five `R` (rename) lines and the `coach/` directory gone.

Run (will fail due to imports): `cd frontend && npx tsc --noEmit`
Expected: Many errors about missing `'./components/coach/CoachDrawer'`, `Coach` import, etc. This is the broken state we will fix in B2.

- [ ] **Step 3: Commit the move**

```bash
git commit -m "$(cat <<'EOF'
refactor(frontend): move coach/ files to chat/ (rename only)

Build is intentionally broken at this commit; B2 fixes imports and
internal symbols.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B2: Rename internal symbols + URL params + telemetry, fix imports

**Files:**
- Modify: `frontend/src/components/chat/Chat.tsx`
- Modify: `frontend/src/components/chat/Chat.test.tsx`
- Modify: `frontend/src/components/chat/ChatDrawer.tsx`
- Modify: `frontend/src/components/chat/ChatDrawer.test.tsx`
- Modify: `frontend/src/components/ui/icons/Chat.tsx`
- Modify: `frontend/src/components/ui/icons/index.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/AppShell.test.tsx`
- Modify: `frontend/src/components/settings/ProfileSummary.tsx`
- Modify: `frontend/src/components/settings/ProfileSummary.test.tsx`
- Modify: `frontend/src/components/feed/ProfileCompletenessCard.tsx`
- Modify: `frontend/src/components/feed/ProfileCompletenessCard.test.tsx`
- Modify: `frontend/src/components/ui/Drawer.test.tsx`

- [ ] **Step 1: Rename the icon export**

Edit `frontend/src/components/ui/icons/Chat.tsx` — replace the entire body with:

```tsx
import { SVGAttributes } from 'react'
export function Chat(props: SVGAttributes<SVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" {...props}>
      <path d="M12 2l1.8 5.4L19 9l-5.2 1.6L12 16l-1.8-5.4L5 9l5.2-1.6L12 2zM19 16l.7 2.1 2.3.7-2.3.7L19 22l-.7-2.1L16 19.2l2.3-.7L19 16zM5 16l.5 1.5L7 18l-1.5.5L5 20l-.5-1.5L3 18l1.5-.5L5 16z" />
    </svg>
  )
}
```

Edit `frontend/src/components/ui/icons/index.ts`. Find:

```ts
export { Coach } from './Coach'
```

Replace with:

```ts
export { Chat } from './Chat'
```

- [ ] **Step 2: Rename the Chat component**

Edit `frontend/src/components/chat/Chat.tsx`:
- Replace `export interface CoachProps` with `export interface ChatProps`.
- Replace `export function Coach({ initialPrompt }: CoachProps)` with `export function Chat({ initialPrompt }: ChatProps)`.
- Replace the comment `'Tell coach →'` (in the `initialPrompt` JSDoc) with `'Open chat →'`.
- Replace every `track('coach.message_sent', ...)` with `track('chat.message_sent', ...)`.
- Replace every `track('coach.message_failed', ...)` with `track('chat.message_failed', ...)`.
- Replace `track('coach.search_now_clicked')` with `track('chat.search_now_clicked')`.

- [ ] **Step 3: Update `Chat.test.tsx`**

Edit `frontend/src/components/chat/Chat.test.tsx`:
- Replace `import { Coach }` with `import { Chat }`.
- Replace `describe('Coach', ...)` with `describe('Chat', ...)`.
- Replace every `<Coach ... />` JSX usage with `<Chat ... />`.

- [ ] **Step 4: Rename the ChatDrawer**

Edit `frontend/src/components/chat/ChatDrawer.tsx` — replace its entire body with:

```tsx
import { useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Drawer } from '../ui/Drawer'
import { Chat } from './Chat'
import { track } from '../../lib/track'

const PROMPT_BY_SLUG: Record<string, string> = {
  set_resume:    'Help me upload or describe my resume.',
  set_roles:     'What roles am I targeting?',
  set_locations: 'Where am I open to working? Any locations or remote-only?',
  set_keywords:  'What technologies / keywords matter most for my search?',
  change_profile: 'I want to change something in my profile.',
}

export function ChatDrawer() {
  const [params, setParams] = useSearchParams()
  const open = params.get('chat') === '1'
  const slug = params.get('prompt')
  const initialPrompt = slug ? PROMPT_BY_SLUG[slug] : undefined

  useEffect(() => {
    if (open) {
      track('chat.opened', { source: 'deep_link', prompt_slug: slug ?? null })
    }
  }, [open, slug])

  function close() {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.delete('chat')
      next.delete('prompt')
      return next
    }, { replace: true })
  }

  return (
    <Drawer open={open} onClose={close} title="Chat">
      <Chat initialPrompt={initialPrompt} />
    </Drawer>
  )
}
```

- [ ] **Step 5: Update `ChatDrawer.test.tsx`**

Edit `frontend/src/components/chat/ChatDrawer.test.tsx`:
- Replace `import { CoachDrawer } from './CoachDrawer'` with `import { ChatDrawer } from './ChatDrawer'`.
- Replace `<CoachDrawer />` with `<ChatDrawer />`.
- Replace `describe('CoachDrawer', ...)` with `describe('ChatDrawer', ...)`.
- Replace every `'?coach=1'` and `'?coach='` and `coach=1` literal in route paths with `?chat=1` / `chat=1`.
- Replace `aria-label', 'Coach'` with `aria-label', 'Chat'`.
- Replace `'renders nothing when ?coach is absent'` with `'renders nothing when ?chat is absent'`.
- Replace `'renders the drawer when ?coach=1 is present'` with `'renders the drawer when ?chat=1 is present'`.
- Replace `'closing the drawer removes ?coach from the URL'` with `'closing the drawer removes ?chat from the URL'`.
- Replace `'passes a known prompt slug as initialPrompt to Coach'` with `'passes a known prompt slug as initialPrompt to Chat'`.

- [ ] **Step 6: Update `App.tsx`**

Edit `frontend/src/App.tsx`. Find:

```tsx
import { CoachDrawer } from './components/coach/CoachDrawer'
```

Replace with:

```tsx
import { ChatDrawer } from './components/chat/ChatDrawer'
```

And replace `<CoachDrawer />` with `<ChatDrawer />`.

- [ ] **Step 7: Update `AppShell.tsx`**

Edit `frontend/src/components/AppShell.tsx`:
- Replace `import { Settings, Coach, Hamburger, Sync } from './ui/icons'` with `import { Settings, Chat, Hamburger, Sync } from './ui/icons'`.
- Replace `function openCoach()` with `function openChat()`.
- Inside that function, replace `next.set('coach', '1')` with `next.set('chat', '1')`.
- Replace `<IconButton aria-label="Coach" onClick={openCoach}>` with `<IconButton aria-label="Chat" onClick={openChat}>`.
- Replace `<Coach className="w-5 h-5" />` with `<Chat className="w-5 h-5" />`.
- Replace `onClick={() => { setMenuOpen(false); openCoach() }}` with `onClick={() => { setMenuOpen(false); openChat() }}`.
- Replace the menu item label `Coach` (the standalone text inside `ActionSheetItem`) with `Chat`.

- [ ] **Step 8: Update `AppShell.test.tsx`**

Edit `frontend/src/components/AppShell.test.tsx`:
- Replace `import { CoachDrawer } from './coach/CoachDrawer'` with `import { ChatDrawer } from './chat/ChatDrawer'`.
- Replace `function renderShellWithCoach` with `function renderShellWithChat`.
- Replace `<CoachDrawer />` with `<ChatDrawer />`.
- Replace `'renders Sync, Settings, Coach, Sign-out controls (desktop bar)'` with `'renders Sync, Settings, Chat, Sign-out controls (desktop bar)'`.
- Replace `getByRole('button', { name: /coach/i })` (both occurrences) with `getByRole('button', { name: /chat/i })`.
- Replace `'opens the Coach drawer when Coach is clicked'` with `'opens the Chat drawer when Chat is clicked'`.
- Replace `renderShellWithCoach('/')` with `renderShellWithChat('/')`.
- Replace `getByRole('dialog', { name: 'Coach' })` with `getByRole('dialog', { name: 'Chat' })`.

- [ ] **Step 9: Update `ProfileSummary.tsx`**

Edit `frontend/src/components/settings/ProfileSummary.tsx`. Find:

```tsx
to="?coach=1&prompt=change_profile"
```

Replace with:

```tsx
to="?chat=1&prompt=change_profile"
```

Find the visible label `✦ Open Coach` and replace with `✦ Open Chat`.

- [ ] **Step 10: Update `ProfileSummary.test.tsx`**

Edit `frontend/src/components/settings/ProfileSummary.test.tsx`:
- Replace `'Open Coach CTA links to ?coach=1&prompt=change_profile'` with `'Open Chat CTA links to ?chat=1&prompt=change_profile'`.
- Replace `getByRole('link', { name: /open coach/i })` with `getByRole('link', { name: /open chat/i })`.
- Replace `toMatch(/coach=1/)` with `toMatch(/chat=1/)`.

- [ ] **Step 11: Update `ProfileCompletenessCard.tsx`**

Edit `frontend/src/components/feed/ProfileCompletenessCard.tsx`. Find:

```tsx
to={`/?coach=1&prompt=${c.promptSlug}`}
```

Replace with:

```tsx
to={`/?chat=1&prompt=${c.promptSlug}`}
```

Find the visible label `Tell coach →` and replace with `Open chat →`.

- [ ] **Step 12: Update `ProfileCompletenessCard.test.tsx`**

Edit `frontend/src/components/feed/ProfileCompletenessCard.test.tsx`. Replace every assertion that mentions "Tell coach" with the equivalent "Open chat" assertion. Specifically:
- Replace `'shows "Tell coach" CTA when in setup state'` with `'shows "Open chat" CTA when in setup state'`.
- Replace any `getByText(/tell coach/i)` with `getByText(/open chat/i)`.
- Replace any `toMatch(/coach=1/)` with `toMatch(/chat=1/)` and any `'?coach=1'` literal with `'?chat=1'`.

- [ ] **Step 13: Update `Drawer.test.tsx`**

Edit `frontend/src/components/ui/Drawer.test.tsx`:
- Replace `title="Coach"` (in both `<Drawer />` test fixtures) with `title="Test drawer"`.
- Replace `aria-label', 'Coach'` with `aria-label', 'Test drawer'`.

- [ ] **Step 14: Verify the build is clean**

Run: `cd frontend && npx tsc --noEmit`
Expected: PASS — zero errors.

Run: `cd frontend && npx vitest run`
Expected: PASS — all tests green.

- [ ] **Step 15: Verify no `coach` references remain in `frontend/src/`**

Run: `rg -wi 'coach' frontend/src`
Expected: zero hits. If any remain, fix them and re-run before committing.

- [ ] **Step 16: Commit**

```bash
git add frontend/src/
git commit -m "$(cat <<'EOF'
refactor(frontend): rename Coach → Chat (UI labels, URL, telemetry)

URL param ?coach=1 → ?chat=1. Drawer title, IconButton aria-label,
Settings CTA, ProfileCompletenessCard CTA all read "Chat" / "Open
chat →". Telemetry events coach.* → chat.*. No backend changes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task B3: Brand "Job Search"

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/components/AppShell.tsx`
- Modify: `frontend/src/components/AppShell.test.tsx`
- Modify: `frontend/src/pages/Landing.tsx`
- Modify: `frontend/e2e/auth-and-nav.spec.ts`

- [ ] **Step 1: Update `index.html`**

Edit `frontend/index.html`. Find:

```html
<title>Job Application Agent</title>
```

Replace with:

```html
<title>Job Search</title>
```

- [ ] **Step 2: Update the `AppShell` brand link**

Edit `frontend/src/components/AppShell.tsx`. Find:

```tsx
<Link to="/" className="font-bold text-text text-sm tracking-tight">Job Agent</Link>
```

Replace with:

```tsx
<Link to="/" className="font-bold text-text text-sm tracking-tight">Job Search</Link>
```

- [ ] **Step 3: Update the `AppShell` test brand assertion**

Edit `frontend/src/components/AppShell.test.tsx`. Find:

```tsx
const brand = screen.getByText('Job Agent')
```

Replace with:

```tsx
const brand = screen.getByText('Job Search')
```

- [ ] **Step 4: Update the landing hero**

Edit `frontend/src/pages/Landing.tsx`. Find:

```tsx
<h1 className="text-3xl font-bold text-text mb-3 tracking-tight">Job Application Agent</h1>
```

Replace with:

```tsx
<h1 className="text-3xl font-bold text-text mb-3 tracking-tight">Job Search</h1>
```

- [ ] **Step 5: Update e2e assertions**

Edit `frontend/e2e/auth-and-nav.spec.ts`. Find:

```ts
await expect(page.getByRole('heading', { name: /Job Application Agent/i })).toBeVisible()
```

Replace with:

```ts
await expect(page.getByRole('heading', { name: /Job Search/i })).toBeVisible()
```

Find:

```ts
await expect(page.getByRole('link', { name: 'Job Agent' })).toHaveAttribute('href', '/')
```

Replace with:

```ts
await expect(page.getByRole('link', { name: 'Job Search' })).toHaveAttribute('href', '/')
```

- [ ] **Step 6: Verify**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: all green.

Run: `rg 'Job (Agent|Application Agent)' frontend/`
Expected: zero hits.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "$(cat <<'EOF'
refactor(frontend): rebrand chrome to "Job Search"

<title>, AppShell brand link, landing hero, and matching test +
e2e assertions all read "Job Search".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track C — Undismiss

### Task C1: Widen `reviewApplication` signature; remove stale comment + cast

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/pages/ApplicationReview.tsx`

- [ ] **Step 1: Widen the client signature**

Edit `frontend/src/api/client.ts`. Find:

```ts
reviewApplication: (id: string, status: 'dismissed' | 'applied') =>
  apiFetch<{ id: string; status: string }>(`/api/applications/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  }),
```

Replace with:

```ts
reviewApplication: (id: string, status: 'dismissed' | 'applied' | 'pending_review') =>
  apiFetch<{ id: string; status: string }>(`/api/applications/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  }),
```

- [ ] **Step 2: Drop stale comment + cast in `ApplicationReview.tsx`**

Edit `frontend/src/pages/ApplicationReview.tsx`. Find:

```tsx
  const moveBackToPending = useMutation({
    mutationFn: async () => {
      // Backend currently accepts only 'dismissed' or 'applied' on this PATCH;
      // 'pending_review' will error-toast until a backend follow-up extends
      // the API. Cast bypasses the client type so the wire intent is clear.
      return api.reviewApplication(id!, 'pending_review' as 'dismissed' | 'applied')
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
    onError: (e) => show((e as Error)?.message ?? 'Backend does not yet allow un-applying', 'error'),
  })
```

Replace with:

```tsx
  const moveBackToPending = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'pending_review'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not move back to pending', 'error'),
  })
```

- [ ] **Step 3: Verify**

Run: `cd frontend && npx tsc --noEmit && npx vitest run`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/pages/ApplicationReview.tsx
git commit -m "$(cat <<'EOF'
refactor(frontend): allow reviewApplication(pending_review)

Backend has accepted pending_review since applications.py:143; the
stale comment + cast in ApplicationReview suggested otherwise. Drops
both, widens the client type union, and updates the error-toast copy
since the move-back is no longer expected to fail.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task C2: Restore on `MatchCard` (Dismissed tab); swipe no-op

**Files:**
- Modify: `frontend/src/components/feed/MatchCard.tsx`
- Modify: `frontend/src/components/feed/MatchCard.test.tsx`

- [ ] **Step 1: Add the failing tests**

Add these tests to `frontend/src/components/feed/MatchCard.test.tsx` inside the existing `describe('MatchCard')` block, after the existing tests:

```tsx
it('renders Restore (not Dismiss) in the kebab when status is dismissed', async () => {
  const user = userEvent.setup()
  renderCard(makeApp({ status: 'dismissed' }))
  await user.click(screen.getByRole('button', { name: /more actions/i }))
  expect(screen.getByText(/restore/i)).toBeInTheDocument()
  expect(screen.queryByText(/^dismiss$/i)).not.toBeInTheDocument()
})

it('clicking Restore in the kebab POSTs status=pending_review', async () => {
  let patched: unknown = null
  server.use(
    http.patch('/api/applications/app-1', async ({ request }) => {
      patched = await request.json()
      return HttpResponse.json({ id: 'app-1', status: 'pending_review' })
    }),
  )
  const user = userEvent.setup()
  renderCard(makeApp({ status: 'dismissed' }))
  await user.click(screen.getByRole('button', { name: /more actions/i }))
  await user.click(screen.getByText(/restore/i))
  await waitFor(() => expect(patched).toEqual({ status: 'pending_review' }))
})

it('swipe-left on a dismissed card does NOT fire any PATCH', async () => {
  let patchCount = 0
  server.use(
    http.patch('/api/applications/app-1', () => {
      patchCount += 1
      return HttpResponse.json({ id: 'app-1', status: 'dismissed' })
    }),
  )
  renderCard(makeApp({ status: 'dismissed' }))
  const surface = screen.getByTestId('swipe-surface')
  fireEvent.pointerDown(surface, { clientX: 200, pointerId: 1 })
  fireEvent.pointerMove(surface, { clientX: 100, pointerId: 1 })
  fireEvent.pointerUp(surface, { clientX: 100, pointerId: 1 })
  await new Promise((r) => setTimeout(r, 30))
  expect(patchCount).toBe(0)
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/components/feed/MatchCard.test.tsx -t "Restore"`
Expected: FAIL — "Restore" not found in DOM, swipe still PATCHes.

- [ ] **Step 3: Implement Restore mutation + kebab branching + swipe no-op**

Edit `frontend/src/components/feed/MatchCard.tsx`. Replace its entire body with:

```tsx
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Application } from '../../api/client'
import { track } from '../../lib/track'
import { Card } from '../ui/Card'
import { IconButton } from '../ui/IconButton'
import { ActionSheet, ActionSheetItem } from '../ui/ActionSheet'
import { SwipeableCard } from '../ui/SwipeableCard'
import { Kebab } from '../ui/icons'
import { useToast } from '../ui/Toast'
import { ScoreBadge } from './ScoreBadge'
import { GenerationBadge } from './GenerationBadge'

function relativeAge(iso: string | null): string {
  if (!iso) return ''
  const ms = Date.now() - new Date(iso).getTime()
  const d = Math.floor(ms / 86_400_000)
  if (d <= 0) return 'today'
  if (d === 1) return '1d ago'
  if (d < 30) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}

export function MatchCard({ app }: { app: Application }) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [menuOpen, setMenuOpen] = useState(false)
  const isDismissed = app.status === 'dismissed'

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'dismissed'),
    onSuccess: () => {
      show(`Dismissed ${app.job?.title ?? 'match'}`, 'info')
      qc.invalidateQueries({ queryKey: ['applications'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  const restore = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'pending_review'),
    onSuccess: () => {
      show(`Restored ${app.job?.title ?? 'match'}`, 'info')
      qc.invalidateQueries({ queryKey: ['applications'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not restore', 'error'),
  })

  const job = app.job
  if (!job) return null

  const meta = [job.location, job.workplace_type, job.salary].filter(Boolean).join(' · ')
  const topStrength = app.match_strengths?.[0]
  const topGap = app.match_gaps?.[0]
  const age = relativeAge(job.posted_at) || relativeAge(app.created_at)

  return (
    <SwipeableCard
      onCommit={() => {
        if (isDismissed) return
        track('match.dismissed', { application_id: app.id, source: 'swipe', score: app.match_score })
        dismiss.mutate()
      }}
      actionLabel="Remove"
    >
      <div className="relative">
        <div className="absolute top-1 right-1 z-10">
          <IconButton
            aria-label="More actions"
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setMenuOpen(true) }}
          >
            <Kebab className="w-4 h-4" />
          </IconButton>
        </div>

        <Card as="rrlink" to={`/matches/${app.id}`} interactive onClick={() => track('match.card_opened', { application_id: app.id, score: app.match_score })} className="block pr-12">
          <div className="flex items-center gap-2 flex-wrap">
            <ScoreBadge score={app.match_score} />
            <GenerationBadge status={app.generation_status} />
            {age && <span className="ml-auto text-xs text-subtle font-mono">{age}</span>}
          </div>
          <h3 className="mt-2 text-base font-bold text-text tracking-tight truncate">{job.title}</h3>
          <p className="text-sm text-text">{job.company_name}</p>
          {meta && <p className="text-xs text-subtle font-mono mt-1">{meta}</p>}
          {(topStrength || topGap) && (
            <p className="text-xs text-muted mt-2 pt-2 border-t border-border">
              {topStrength && <><span className="text-success font-semibold">Strong:</span> {topStrength}</>}
              {topStrength && topGap && <span className="mx-1">·</span>}
              {topGap && <><span className="text-warning font-semibold">Gap:</span> {topGap}</>}
            </p>
          )}
        </Card>

        <ActionSheet open={menuOpen} onClose={() => setMenuOpen(false)} title="Match actions" heading={job.title}>
          <ActionSheetItem onClick={() => { setMenuOpen(false); show('Saved for later', 'info') }}>
            Save for later
          </ActionSheetItem>
          <ActionSheetItem onClick={() => {
            setMenuOpen(false)
            window.open(job.apply_url, '_blank', 'noopener')
          }}>
            Open original posting ↗
          </ActionSheetItem>
          {isDismissed ? (
            <ActionSheetItem onClick={() => {
              setMenuOpen(false)
              track('match.undismissed', { application_id: app.id, source: 'kebab' })
              restore.mutate()
            }}>
              Restore
            </ActionSheetItem>
          ) : (
            <ActionSheetItem intent="danger" onClick={() => {
              setMenuOpen(false)
              track('match.dismissed', { application_id: app.id, source: 'kebab', score: app.match_score })
              dismiss.mutate()
            }}>
              Dismiss
            </ActionSheetItem>
          )}
        </ActionSheet>
      </div>
    </SwipeableCard>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/components/feed/MatchCard.test.tsx`
Expected: PASS — all existing + new tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/feed/MatchCard.tsx frontend/src/components/feed/MatchCard.test.tsx
git commit -m "$(cat <<'EOF'
feat(matches): Restore action on dismissed MatchCard kebab

Replaces the meaningless "Dismiss" item on already-dismissed cards
with a "Restore" item that POSTs status=pending_review. Swipe-to-
dismiss is a no-op on dismissed cards (it previously fired a wasted
PATCH against an already-dismissed record).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task C3: Restore-to-pending in `ApplicationReview` kebab (when dismissed)

**Files:**
- Modify: `frontend/src/pages/ApplicationReview.tsx`
- Modify: `frontend/src/pages/ApplicationReview.test.tsx`

- [ ] **Step 1: Add the failing test**

Add this test to `frontend/src/pages/ApplicationReview.test.tsx` inside the `describe('Match detail (ApplicationReview)')` block, after the existing tests:

```tsx
it('shows "Restore to pending" in the kebab when status is dismissed and POSTs pending_review', async () => {
  let patched: unknown = null
  server.use(
    http.patch('/api/applications/a1', async ({ request }) => {
      patched = await request.json()
      return HttpResponse.json({ id: 'a1', status: 'pending_review' })
    }),
  )
  renderAt('/matches/a1', detail({ status: 'dismissed' }))
  await waitFor(() => expect(screen.getByRole('heading', { name: /senior backend engineer/i })).toBeInTheDocument())
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: /more actions/i }))
  await user.click(screen.getByText(/restore to pending/i))
  await waitFor(() => expect(patched).toEqual({ status: 'pending_review' }))
})
```

Add the imports at the top of the file if not already present:

```tsx
import userEvent from '@testing-library/user-event'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/pages/ApplicationReview.test.tsx -t "Restore to pending"`
Expected: FAIL — "Restore to pending" not found in the kebab.

- [ ] **Step 3: Extend the kebab gate**

Edit `frontend/src/pages/ApplicationReview.tsx`. Find the `<ActionSheet>` block (currently lines 88-106 area) and replace the inner `applied` block with:

```tsx
        {(app.status === 'applied' || app.status === 'dismissed') && (
          <ActionSheetItem onClick={() => {
            setMenuOpen(false)
            if (app.status === 'dismissed') {
              track('match.undismissed', { application_id: id, source: 'detail_kebab' })
            } else {
              track('match.unapplied', { application_id: id })
            }
            moveBackToPending.mutate()
          }}>
            {app.status === 'applied' ? 'Move back to pending' : 'Restore to pending'}
          </ActionSheetItem>
        )}
        {app.status !== 'dismissed' && (
          <ActionSheetItem intent="danger" onClick={() => { setMenuOpen(false); dismiss.mutate() }}>
            Dismiss
          </ActionSheetItem>
        )}
```

(Keep the unchanged `Open original posting ↗` `ActionSheetItem` above it.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/pages/ApplicationReview.test.tsx`
Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ApplicationReview.tsx frontend/src/pages/ApplicationReview.test.tsx
git commit -m "$(cat <<'EOF'
feat(match-detail): Restore to pending on dismissed apps

Extends the existing moveBackToPending kebab item gate so it also
fires when the application is dismissed. Label flips between
"Move back to pending" (applied) and "Restore to pending"
(dismissed). Telemetry source is 'detail_kebab' for the dismissed
path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

# Track D — Verification & PR

### Task D1: Full verification (typecheck, lint, tests)

- [ ] **Step 1: Run TypeScript**

Run: `cd frontend && npx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 2: Run lint**

Run: `cd frontend && npm run lint`
Expected: zero errors. If lint fails, fix the reported issues and re-run before continuing.

- [ ] **Step 3: Run unit tests**

Run: `cd frontend && npx vitest run`
Expected: all tests pass.

- [ ] **Step 4: Run rename verification grep**

Run: `rg -wi 'coach' frontend/src`
Expected: zero hits.

Run: `rg 'Job (Agent|Application Agent)' frontend/`
Expected: zero hits.

Run: `rg -n 'coach=1' app/ alembic/ scripts/`
Expected: zero hits (Coach is a frontend-only term; if non-zero, something needs coordination — stop and surface it).

---

### Task D2: Manual dev smoke + screenshots

- [ ] **Step 1: Start backend + frontend**

In one terminal: `docker compose up -d db && uv run uvicorn app.main:app --reload --port 8000`
In another: `cd frontend && npm run dev`

- [ ] **Step 2: Capture desktop screenshots**

Open the app in a desktop-sized browser window (≥768px wide) and capture screenshots of each of the following. Save to `tmp/screenshots/`:

1. The header showing the rebranded "Job Search" link.
2. The header showing the Chat icon button (replacing what was Coach).
3. The Chat drawer opened (URL has `?chat=1`).
4. An `ApplicationReview` page for a `pending_review` application — capture the header showing both "Open posting ↗" CTA and the kebab.
5. The Matches Dismissed tab (`?status=dismissed`) showing a `MatchCard`.
6. The Dismissed-tab `MatchCard` kebab opened, showing "Restore" (not "Dismiss").
7. An `ApplicationReview` page for a `dismissed` application with the kebab opened, showing "Restore to pending".

- [ ] **Step 3: Spot-check telemetry**

Open dev tools → Network panel. Open the Chat drawer; confirm any analytics requests reflect the new event names (`chat.opened`, etc.) — or, if telemetry is logged-only in dev, confirm the console emits `chat.*` events. (Skip if telemetry is opaque in dev; it's covered by unit tests.)

---

### Task D3: Open the PR

- [ ] **Step 1: Push the branch**

```bash
git status
git push -u origin HEAD
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "feat(matches): desktop apply CTA + Coach→Chat + Job Search rebrand + undismiss" --body "$(cat <<'EOF'
## Summary

Four scoped frontend UX fixes bundled into one PR (single shared surface area: matches list, application review, app shell):

- **Desktop apply CTA.** `ApplicationReview` now shows a primary "Open posting ↗" button in the header on `md+` viewports, hidden on mobile. Clicking opens the URL in a new tab and (for pending_review) marks the application applied. The mobile `StickyActions` bottom bar is unchanged. Both surfaces share a new `useApplyAction` hook.
- **Coach → Chat.** The chat modal — component, file path, drawer title, IconButton aria-label, URL param (`?chat=1`), telemetry events (`chat.*`), and CTAs in Settings + ProfileCompletenessCard — all read "Chat". The CTA "Tell coach →" reads "Open chat →".
- **Job Search rebrand.** `<title>`, header brand link, and landing hero all read "Job Search" instead of "Job Application Agent" / "Job Agent".
- **Undismiss.** Dismissed matches can be restored to pending at any time. The Dismissed-tab `MatchCard` kebab replaces "Dismiss" with "Restore"; swipe-to-dismiss is a no-op on dismissed cards. The `ApplicationReview` kebab gains a "Restore to pending" item alongside the existing "Move back to pending" (applied). The stale `'pending_review' as 'dismissed' | 'applied'` cast and its incorrect comment are removed; the backend has accepted `pending_review` since `applications.py:143`.

## Test plan

- [x] `cd frontend && npx tsc --noEmit` — zero errors.
- [x] `cd frontend && npm run lint` — zero errors.
- [x] `cd frontend && npx vitest run` — all tests pass.
- [x] `rg -wi 'coach' frontend/src` — zero hits.
- [x] `rg 'Job (Agent|Application Agent)' frontend/` — zero hits.
- [x] Manual desktop smoke: header CTA opens posting + marks applied, Chat drawer opens via header icon, ProfileCompletenessCard "Open chat →" deep link works, Dismissed tab kebab shows Restore, detail-page kebab shows Restore to pending on a dismissed app.
- [x] Screenshots attached below.

## Screenshots

[Attach the 7 screenshots from `tmp/screenshots/`.]

## Notes

- Telemetry event prefix `coach.*` is renamed to `chat.*`. Any analytics dashboard built on these will see a discontinuity; user accepted this tradeoff.
- Backend, repo name, and backend module names (`app/agents/`) are intentionally not renamed — the rebrand is product-facing only.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Attach screenshots to the PR**

Drag/drop the seven files from `tmp/screenshots/` into the PR description on GitHub. Replace the `[Attach the 7 screenshots…]` placeholder with the resulting markdown image links.

- [ ] **Step 4: Watch CI**

```bash
gh pr checks --watch
```

Expected: green. If anything fails, fix the underlying issue and push a follow-up commit.

---

## Self-review (run before handoff)

The plan was checked against the spec. Coverage by track:

- **Spec Part 1 (Desktop apply CTA)** — Tasks A1–A4 (hook, button, wire, refactor).
- **Spec Part 2a (Coach → Chat)** — Tasks B1–B2.
- **Spec Part 2b (Job Search rebrand)** — Task B3.
- **Spec Part 3 (Undismiss)** — Tasks C1–C3.
- **Spec Verification & PR** — Tasks D1–D3.

No placeholders remain. Hook signature `useApplyAction({ appId, status, applyUrl }) → { onOpen, isApplied, markApplied }` is consistent across A1, A2, A4. URL param `?chat=1` and event prefix `chat.*` are consistent across B2 and the test assertions in B2. The undismiss path uses `api.reviewApplication(id, 'pending_review')` consistently — matching the widened type from C1.
