# Frontend UX Redesign — Plan B: Pages (Feed + Match Detail) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `Matches.tsx` and `ApplicationReview.tsx` with the new Feed and Match Detail pages defined in spec sections 4–5. Both consume primitives from Plan A (`Button`, `Chip`, `Card`, `ActionSheet`, `Toast`, `Badge`, `EmptyState`, `SkeletonCard`, `SwipeableCard`, `TextArea`). Pull-to-refresh, swipe-to-dismiss with kebab fallback, optimistic mark-applied, sticky bottom action bar, profile-completeness card, status filter chips — all live here. Settings, Coach drawer, analytics, and final cleanup are deferred to Plans C and D.

**Architecture:** New page components decompose into focused, colocated sub-components under `src/components/feed/` and `src/components/match-detail/`. Status filter is URL-driven via `?status=` (TanStack Query keys include the param so the cache differentiates). The new `MatchCard` uses `Card as="rrlink"` so the entire card is a real anchor, with the `SwipeableCard` wrapper handling left-swipe dismiss and a `Kebab → ActionSheet` fallback. Optimistic updates are TanStack Query mutations with `onMutate` rolling back on error and a `Toast` reporting outcomes. Old `MatchCard.tsx` and `SyncStatusChip.tsx` are deleted in this plan because their consumers (Matches.tsx, ApplicationReview.tsx) are being rewritten and they have no other callers.

**Tech Stack:** React 18 + Vite + TypeScript + TanStack Query v5 + React Router v6 + Tailwind 3 + Vitest + @testing-library/react + jsdom. No new runtime deps.

**Spec:** `docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md` sections 4 (Feed) and 5 (Match detail). Plan A's `feat/ui-foundation` (PR #93) must be merged to `main` before this plan executes.

**Branching:** Implementation lives on `feat/ui-pages`, branched from `main` after Plan A merges.

---

## File Structure

**Files to be created:**

```
frontend/src/lib/useStatusFilter.ts              URL-driven ?status= reader/setter hook
frontend/src/lib/useStatusFilter.test.ts
frontend/src/components/feed/StatusChips.tsx     Pending / Applied / Dismissed chip row
frontend/src/components/feed/StatusChips.test.tsx
frontend/src/components/feed/SyncRow.tsx         Sync button + live status copy + idle hint
frontend/src/components/feed/SyncRow.test.tsx
frontend/src/components/feed/ScoreBadge.tsx      Score band → success / warning / muted Badge
frontend/src/components/feed/ScoreBadge.test.tsx
frontend/src/components/feed/GenerationBadge.tsx Cover-letter status → Badge
frontend/src/components/feed/MatchCard.tsx       New card (Card + SwipeableCard + Kebab + ActionSheet)
frontend/src/components/feed/MatchCard.test.tsx
frontend/src/components/feed/ProfileCompletenessCard.tsx
frontend/src/components/feed/ProfileCompletenessCard.test.tsx
frontend/src/components/match-detail/MatchHero.tsx       Title / company / meta line
frontend/src/components/match-detail/MatchHero.test.tsx
frontend/src/components/match-detail/MatchAnalysis.tsx   Score block + strengths/gaps
frontend/src/components/match-detail/MatchAnalysis.test.tsx
frontend/src/components/match-detail/JobDescription.tsx  Full-width description (no expander)
frontend/src/components/match-detail/JobDescription.test.tsx
frontend/src/components/match-detail/CoverLetterEditor.tsx  Editor + generate / regenerate / pdf
frontend/src/components/match-detail/CoverLetterEditor.test.tsx
frontend/src/components/match-detail/StickyActions.tsx   Mobile-only bottom bar (Skip + Open posting)
frontend/src/components/match-detail/StickyActions.test.tsx
```

**Files to be modified:**

```
frontend/src/pages/Matches.tsx          Rewritten as the new Feed page (filename kept; route stays /matches AND adds /)
frontend/src/pages/ApplicationReview.tsx  Rewritten as the new Match Detail page
frontend/src/App.tsx                     Add `/` → Matches alias; redirect logic for legacy /applied (Plan B keeps the route alive temporarily)
frontend/src/pages/Matches.test.tsx     Updated tests for the rewritten Feed
frontend/src/pages/ApplicationReview.test.tsx  Created (currently no test for this page; new tests cover the rewritten detail page)
```

Note: `frontend/src/pages/Matches.test.tsx` exists today and tests the legacy Matches page; Task 7 fully replaces its contents (the legacy assertions don't apply to the new Feed). `frontend/src/pages/ApplicationReview.test.tsx` does NOT exist today; Task 14 creates it.

**Files to be deleted:**

```
frontend/src/components/MatchCard.tsx          Old card; sole consumer (old Matches.tsx) is rewritten
frontend/src/components/MatchCard.test.tsx     Tests the deleted file
frontend/src/components/SyncStatusChip.tsx     Replaced by SyncRow component
frontend/src/components/SyncStatusChip.test.tsx  if it exists; verify and skip if absent
```

**Files NOT touched (deferred to Plans C / D):**

- `Onboarding.tsx`, `Applied.tsx`, `Landing.tsx`, `BudgetBanner.tsx`, `InvalidSlugsNotice.tsx`
- Coach drawer wiring (Plan C)
- Settings page (Plan C)
- Analytics events (Plan D)
- `/applied` and `/profile` route deletes (Plan D — fold into single feed and Settings page respectively)

---

## Task 0: Setup branch and baseline

**Files:** none

- [ ] **Step 1: Confirm clean working tree on main, up to date**

```bash
cd /Users/panibrat/dev/job-application-agent
git status
git fetch origin main
git switch main
git pull --ff-only origin main
git log --oneline -3
```

Expected: clean tree, on main, top commit is the merged Plan A. If Plan A has not merged yet, abort and merge it first.

- [ ] **Step 2: Create feature branch**

```bash
git switch --create feat/ui-pages
```

- [ ] **Step 3: Capture baseline**

```bash
cd frontend
npm install
npm run test
```

Expected: 109 tests pass (Plan A baseline). Note this number; subsequent test counts in this plan are net-additive.

```bash
npx tsc --noEmit
```

Expected: no type errors.

```bash
npm run build
```

Expected: build succeeds.

- [ ] **Step 4: Verify primitives + tokens are in place**

```bash
ls frontend/src/components/ui/
ls frontend/src/styles/tokens.css
ls frontend/src/lib/cn.ts
```

Expected: all 13 primitive `.tsx` files present, plus `icons/`, `tokens.css`, and `cn.ts`. If any are missing, Plan A did not fully merge — abort and verify.

---

## Task 1: useStatusFilter hook

**Files:**
- Create: `frontend/src/lib/useStatusFilter.ts`
- Create: `frontend/src/lib/useStatusFilter.test.tsx`

The Feed page reads/writes the `?status=` URL param. A small hook makes this single-sourced and testable in isolation.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { useStatusFilter } from './useStatusFilter'

function Probe() {
  const { status, setStatus } = useStatusFilter()
  return (
    <div>
      <span data-testid="status">{status}</span>
      <button onClick={() => setStatus('applied')}>to-applied</button>
      <button onClick={() => setStatus('dismissed')}>to-dismissed</button>
      <button onClick={() => setStatus('pending')}>to-pending</button>
    </div>
  )
}

function renderWith(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Probe />
    </MemoryRouter>
  )
}

describe('useStatusFilter', () => {
  it('defaults to "pending" when no ?status= is present', () => {
    renderWith('/')
    expect(screen.getByTestId('status').textContent).toBe('pending')
  })

  it('reads ?status=applied from the URL', () => {
    renderWith('/?status=applied')
    expect(screen.getByTestId('status').textContent).toBe('applied')
  })

  it('reads ?status=dismissed', () => {
    renderWith('/?status=dismissed')
    expect(screen.getByTestId('status').textContent).toBe('dismissed')
  })

  it('coerces unknown values back to "pending"', () => {
    renderWith('/?status=bogus')
    expect(screen.getByTestId('status').textContent).toBe('pending')
  })

  it('setStatus updates the URL param', async () => {
    const user = userEvent.setup()
    renderWith('/')
    await user.click(screen.getByText('to-applied'))
    expect(screen.getByTestId('status').textContent).toBe('applied')
  })

  it('setStatus(pending) removes the param entirely (cleaner URLs)', async () => {
    const user = userEvent.setup()
    renderWith('/?status=applied')
    await user.click(screen.getByText('to-pending'))
    expect(screen.getByTestId('status').textContent).toBe('pending')
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd frontend && npx vitest run src/lib/useStatusFilter.test.tsx
```

- [ ] **Step 3: Implement**

Create `frontend/src/lib/useStatusFilter.ts`:

```ts
import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

export type StatusFilter = 'pending' | 'applied' | 'dismissed'

const VALID: readonly StatusFilter[] = ['pending', 'applied', 'dismissed'] as const

function parse(raw: string | null): StatusFilter {
  if (raw && (VALID as readonly string[]).includes(raw)) return raw as StatusFilter
  return 'pending'
}

export interface UseStatusFilterResult {
  status: StatusFilter
  setStatus: (next: StatusFilter) => void
}

/** URL-driven status filter chip state (reads/writes ?status=).
 *  - "pending" is the default and is omitted from the URL for clean links.
 *  - Unknown ?status values are coerced to "pending". */
export function useStatusFilter(): UseStatusFilterResult {
  const [params, setParams] = useSearchParams()
  const status = parse(params.get('status'))

  const setStatus = useCallback((next: StatusFilter) => {
    setParams(
      (prev) => {
        const out = new URLSearchParams(prev)
        if (next === 'pending') out.delete('status')
        else out.set('status', next)
        return out
      },
      { replace: true },
    )
  }, [setParams])

  return { status, setStatus }
}
```

- [ ] **Step 4: Run, expect 6 PASS**

```bash
npx vitest run src/lib/useStatusFilter.test.tsx
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/useStatusFilter.ts frontend/src/lib/useStatusFilter.test.tsx
git commit -m "feat(frontend): useStatusFilter hook (URL-driven ?status=)

Single-sourced reader/writer for the Feed page's status filter chip.
Defaults to pending; param is omitted entirely when pending for cleaner
URLs. Unknown values coerce back to pending."
```

After commit: 115 tests (109 + 6).

---

## Task 2: StatusChips component

**Files:**
- Create: `frontend/src/components/feed/StatusChips.tsx`
- Create: `frontend/src/components/feed/StatusChips.test.tsx`

The chip row consumes `useStatusFilter` AND a `counts` prop (computed by the page from the loaded application list).

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { StatusChips } from './StatusChips'

function renderChips(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <StatusChips counts={{ pending: 12, applied: 4, dismissed: 8 }} />
    </MemoryRouter>
  )
}

describe('StatusChips', () => {
  it('renders three chips with labels and counts', () => {
    renderChips()
    expect(screen.getByRole('button', { name: /pending/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /applied/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /dismissed/i })).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
  })

  it('marks the active chip via aria-pressed=true based on URL', () => {
    renderChips('/?status=applied')
    expect(screen.getByRole('button', { name: /applied/i })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /pending/i })).toHaveAttribute('aria-pressed', 'false')
  })

  it('defaults pending to active when no status= present', () => {
    renderChips('/')
    expect(screen.getByRole('button', { name: /pending/i })).toHaveAttribute('aria-pressed', 'true')
  })

  it('clicking a chip updates the URL via the hook', async () => {
    const user = userEvent.setup()
    renderChips('/')
    await user.click(screen.getByRole('button', { name: /applied/i }))
    expect(screen.getByRole('button', { name: /applied/i })).toHaveAttribute('aria-pressed', 'true')
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd frontend && npx vitest run src/components/feed/StatusChips.test.tsx
```

- [ ] **Step 3: Implement**

```tsx
import { Chip } from '../ui/Chip'
import { useStatusFilter, StatusFilter } from '../../lib/useStatusFilter'

export interface StatusCounts {
  pending: number
  applied: number
  dismissed: number
}

export interface StatusChipsProps {
  counts: StatusCounts
}

const ITEMS: { value: StatusFilter; label: string }[] = [
  { value: 'pending',   label: 'Pending' },
  { value: 'applied',   label: 'Applied' },
  { value: 'dismissed', label: 'Dismissed' },
]

export function StatusChips({ counts }: StatusChipsProps) {
  const { status, setStatus } = useStatusFilter()
  return (
    <div className="flex gap-2 flex-wrap">
      {ITEMS.map(({ value, label }) => (
        <Chip
          key={value}
          selected={status === value}
          count={counts[value]}
          onClick={() => setStatus(value)}
        >
          {label}
        </Chip>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Run, expect 4 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/feed/StatusChips.tsx frontend/src/components/feed/StatusChips.test.tsx
git commit -m "feat(frontend/feed): StatusChips component

Pending / Applied / Dismissed chip row backed by useStatusFilter and
caller-provided counts. Single-select with aria-pressed semantics."
```

After commit: 119 tests (115 + 4).

---

## Task 3: SyncRow component

**Files:**
- Create: `frontend/src/components/feed/SyncRow.tsx`
- Create: `frontend/src/components/feed/SyncRow.test.tsx`

Replaces the old `SyncStatusChip` + plain "Sync jobs" button. Owns the polling, the live state in the button copy, and the idle hint.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { SyncRow } from './SyncRow'

function renderRow() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SyncRow />
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('SyncRow', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle',
        slugs_total: 0,
        slugs_pending: 0,
        matches_pending: 0,
        last_sync_requested_at: null,
        last_sync_completed_at: new Date().toISOString(),
        last_sync_summary: null,
        invalid_slugs: [],
      })),
    )
  })

  it('renders an idle "Sync now" button by default', async () => {
    renderRow()
    expect(await screen.findByRole('button', { name: /sync now/i })).toBeInTheDocument()
  })

  it('shows "Searching… N of M" when status is syncing', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'syncing', slugs_total: 12, slugs_pending: 5, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
    renderRow()
    expect(await screen.findByText(/searching/i)).toBeInTheDocument()
    expect(await screen.findByText(/7 of 12/i)).toBeInTheDocument()
  })

  it('shows "Scoring N jobs…" when status is matching', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'matching', slugs_total: 0, slugs_pending: 0, matches_pending: 8,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
    renderRow()
    expect(await screen.findByText(/scoring/i)).toBeInTheDocument()
    expect(await screen.findByText(/8 jobs/i)).toBeInTheDocument()
  })

  it('clicking the button triggers POST /api/jobs/sync and shows a success toast on success', async () => {
    server.use(
      http.post('/api/jobs/sync', () => HttpResponse.json({
        status: 'queued', queued_slugs: ['stripe', 'vercel'], matched_now: 5, seeded_defaults: false,
      })),
    )
    const user = userEvent.setup()
    renderRow()
    await user.click(await screen.findByRole('button', { name: /sync now/i }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/searching/i)
    )
  })

  it('shows a danger toast when sync fails', async () => {
    server.use(
      http.post('/api/jobs/sync', () => HttpResponse.json({ detail: 'rate limited' }, { status: 429 })),
    )
    const user = userEvent.setup()
    renderRow()
    await user.click(await screen.findByRole('button', { name: /sync now/i }))
    await waitFor(() =>
      expect(screen.getByRole('status').className).toMatch(/border-l-danger/)
    )
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd frontend && npx vitest run src/components/feed/SyncRow.test.tsx
```

- [ ] **Step 3: Implement**

```tsx
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, SyncStatus } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

const POLL_MS = 3_000

function liveLabel(s: SyncStatus): string {
  if (s.state === 'syncing') {
    const done = s.slugs_total - s.slugs_pending
    return `Searching ${done} of ${s.slugs_total} boards…`
  }
  if (s.state === 'matching') {
    return `Scoring ${s.matches_pending} job${s.matches_pending === 1 ? '' : 's'}…`
  }
  return 'Sync now'
}

export function SyncRow() {
  const qc = useQueryClient()
  const { show } = useToast()
  const [status, setStatus] = useState<SyncStatus | null>(null)
  // Keep the previous state to detect idle transitions and refresh the list.
  const prevState = useRef<SyncStatus['state'] | null>(null)

  useEffect(() => {
    let cancelled = false
    async function poll() {
      try {
        const body = await api.getSyncStatus()
        if (cancelled) return
        setStatus(body)
        if (prevState.current && prevState.current !== 'idle' && body.state === 'idle') {
          qc.invalidateQueries({ queryKey: ['applications'] })
        }
        prevState.current = body.state
      } catch {
        // Silent — the button just stays "Sync now" without live state.
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => { cancelled = true; clearInterval(id) }
  }, [qc])

  const sync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: (data) => {
      show(`Searching now — ${data.matched_now ?? 0} from cache.`, 'success')
      // Trigger an immediate poll soon after; useEffect handles the rest.
      setTimeout(() => qc.invalidateQueries({ queryKey: ['applications'] }), 1500)
    },
    onError: (err) => {
      show((err as Error)?.message ?? 'Sync failed — try again', 'error')
    },
  })

  const isLive = status?.state && status.state !== 'idle'
  const label = status ? liveLabel(status) : 'Sync now'
  const lastSyncCopy = status?.last_sync_completed_at
    ? 'Last synced a few minutes ago · we re-check every few hours'
    : ''

  return (
    <div className="flex items-center justify-between gap-3 mb-2">
      <Button
        variant={isLive ? 'ghost' : 'secondary'}
        pending={sync.isPending}
        disabled={!!isLive}
        onClick={() => sync.mutate()}
        size="sm"
      >
        {sync.isPending ? 'Syncing…' : label}
      </Button>
      {!isLive && lastSyncCopy && (
        <span className="text-xs text-subtle">{lastSyncCopy}</span>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run, expect 5 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/feed/SyncRow.tsx frontend/src/components/feed/SyncRow.test.tsx
git commit -m "feat(frontend/feed): SyncRow component

Replaces SyncStatusChip + bare button. Owns the /api/sync/status poll
(3s), inlines live state into the button copy, fires success / error
toasts on POST /api/jobs/sync. Vague-interval idle hint per spec."
```

After commit: 124 tests (119 + 5).

---

## Task 4: ScoreBadge + GenerationBadge helpers

**Files:**
- Create: `frontend/src/components/feed/ScoreBadge.tsx`
- Create: `frontend/src/components/feed/ScoreBadge.test.tsx`
- Create: `frontend/src/components/feed/GenerationBadge.tsx` (no separate test — too trivial)

Thin wrappers over the `Badge` primitive that map score / generation status into the right `intent`.

- [ ] **Step 1: Failing ScoreBadge test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreBadge } from './ScoreBadge'

describe('ScoreBadge', () => {
  it('returns null when score is null', () => {
    const { container } = render(<ScoreBadge score={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the rounded percentage', () => {
    render(<ScoreBadge score={0.873} />)
    expect(screen.getByText('87% match')).toBeInTheDocument()
  })

  it('uses success intent for ≥80% scores', () => {
    const { container } = render(<ScoreBadge score={0.82} />)
    expect(container.firstChild).toHaveClass('text-success')
  })

  it('uses warning intent for 65–79%', () => {
    const { container } = render(<ScoreBadge score={0.7} />)
    expect(container.firstChild).toHaveClass('text-warning')
  })

  it('uses muted intent for <65%', () => {
    const { container } = render(<ScoreBadge score={0.5} />)
    expect(container.firstChild).toHaveClass('text-muted')
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement ScoreBadge**

```tsx
import { Badge, BadgeIntent } from '../ui/Badge'

function intentForScore(pct: number): BadgeIntent {
  if (pct >= 80) return 'success'
  if (pct >= 65) return 'warning'
  return 'muted'
}

export function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return null
  const pct = Math.round(score * 100)
  return <Badge intent={intentForScore(pct)}>{pct}% match</Badge>
}
```

- [ ] **Step 4: Run, expect 5 PASS**

- [ ] **Step 5: Implement GenerationBadge**

Create `frontend/src/components/feed/GenerationBadge.tsx`:

```tsx
import { Badge } from '../ui/Badge'

export function GenerationBadge({ status }: { status: string }) {
  switch (status) {
    case 'ready':       return <Badge intent="success">Documents ready</Badge>
    case 'generating':
    case 'pending':     return <Badge intent="warning">Generating…</Badge>
    case 'failed':      return <Badge intent="danger">Generation failed</Badge>
    default:            return null
  }
}
```

(No separate test file: GenerationBadge is exercised by MatchCard tests in Task 5.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/feed/ScoreBadge.tsx frontend/src/components/feed/ScoreBadge.test.tsx frontend/src/components/feed/GenerationBadge.tsx
git commit -m "feat(frontend/feed): ScoreBadge + GenerationBadge helpers

Thin wrappers over the Badge primitive. Score band thresholds: 80+
success, 65–79 warning, <65 muted. Generation: ready→success,
generating/pending→warning, failed→danger."
```

After commit: 129 tests (124 + 5).

---

## Task 5: New MatchCard

**Files:**
- Create: `frontend/src/components/feed/MatchCard.tsx`
- Create: `frontend/src/components/feed/MatchCard.test.tsx`

The most user-facing surface in the app. Whole card is a real anchor (`<Card as="rrlink">`); swipe-left dismisses with a 5s undo toast; kebab in top-right opens an ActionSheet with `Save for later · Open original posting ↗ · Dismiss`.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { MatchCard } from './MatchCard'
import type { Application } from '../../api/client'

function makeApp(over: Partial<Application> = {}): Application {
  return {
    id: 'app-1',
    status: 'pending_review',
    generation_status: 'ready',
    match_score: 0.87,
    match_summary: 'Strong stack fit',
    match_rationale: 'extra detail',
    match_strengths: ['Go (5y)', 'Postgres at scale'],
    match_gaps: ['No public ML'],
    created_at: new Date().toISOString(),
    applied_at: null,
    job: {
      id: 'job-1',
      title: 'Senior Backend Engineer',
      company_name: 'Acme Robotics',
      location: 'Berlin',
      workplace_type: 'hybrid',
      salary: '€90k–110k',
      contract_type: 'full-time',
      description_md: '',
      apply_url: 'https://example.com/apply',
      posted_at: null,
    },
    ...over,
  }
}

function renderCard(app: Application) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>
          <MatchCard app={app} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('MatchCard', () => {
  it('renders score, title, company, and meta line', () => {
    renderCard(makeApp())
    expect(screen.getByText('87% match')).toBeInTheDocument()
    expect(screen.getByText('Senior Backend Engineer')).toBeInTheDocument()
    expect(screen.getByText('Acme Robotics')).toBeInTheDocument()
    expect(screen.getByText(/berlin/i)).toBeInTheDocument()
  })

  it('renders generation badge when generation_status="ready"', () => {
    renderCard(makeApp({ generation_status: 'ready' }))
    expect(screen.getByText(/documents ready/i)).toBeInTheDocument()
  })

  it('the card is a real anchor pointing to /matches/:id', () => {
    renderCard(makeApp())
    const link = screen.getByRole('link', { name: /senior backend engineer/i })
    expect(link).toHaveAttribute('href', '/matches/app-1')
  })

  it('renders top strength and top gap on the footer line', () => {
    renderCard(makeApp())
    expect(screen.getByText(/Go \(5y\)/i)).toBeInTheDocument()
    expect(screen.getByText(/No public ML/i)).toBeInTheDocument()
  })

  it('opens the kebab action sheet with Save / Open posting / Dismiss', async () => {
    const user = userEvent.setup()
    renderCard(makeApp())
    await user.click(screen.getByRole('button', { name: /more actions/i }))
    expect(screen.getByText(/save for later/i)).toBeInTheDocument()
    expect(screen.getByText(/open original posting/i)).toBeInTheDocument()
    expect(screen.getByText(/dismiss/i)).toBeInTheDocument()
  })

  it('clicking Dismiss in the kebab triggers PATCH and shows undo toast', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/applications/app-1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'app-1', status: 'dismissed' })
      }),
    )
    const user = userEvent.setup()
    renderCard(makeApp())
    await user.click(screen.getByRole('button', { name: /more actions/i }))
    await user.click(screen.getByText(/dismiss/i))
    await waitFor(() => expect(patched).toEqual({ status: 'dismissed' }))
    expect(screen.getByRole('status')).toHaveTextContent(/dismissed/i)
  })

  it('swipe-left past threshold triggers dismissal', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/applications/app-1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'app-1', status: 'dismissed' })
      }),
    )
    renderCard(makeApp())
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 200, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerUp(surface, { clientX: 100, pointerId: 1 })
    await waitFor(() => expect(patched).toEqual({ status: 'dismissed' }))
  })

  it('returns null when job is missing (defensive)', () => {
    const { container } = renderCard(makeApp({ job: null }))
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

```bash
cd frontend && npx vitest run src/components/feed/MatchCard.test.tsx
```

- [ ] **Step 3: Implement**

```tsx
import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Application } from '../../api/client'
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

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(app.id, 'dismissed'),
    onSuccess: () => {
      show(`Dismissed ${app.job?.title ?? 'match'}`, 'info')
      qc.invalidateQueries({ queryKey: ['applications'] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  const job = app.job
  if (!job) return null

  const meta = [job.location, job.workplace_type, job.salary].filter(Boolean).join(' · ')
  const topStrength = app.match_strengths?.[0]
  const topGap = app.match_gaps?.[0]
  const age = relativeAge(job.posted_at) || relativeAge(app.created_at)

  return (
    <SwipeableCard onCommit={() => dismiss.mutate()} actionLabel="Dismiss">
      <div className="relative">
        {/* Kebab in absolute corner — far from natural tap zone, doesn't interfere with the card link. */}
        <div className="absolute top-1 right-1 z-10">
          <IconButton
            aria-label="More actions"
            onClick={(e) => { e.preventDefault(); e.stopPropagation(); setMenuOpen(true) }}
          >
            <Kebab className="w-4 h-4" />
          </IconButton>
        </div>

        <Card as="rrlink" to={`/matches/${app.id}`} interactive className="block pr-12">
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
          <ActionSheetItem intent="danger" onClick={() => { setMenuOpen(false); dismiss.mutate() }}>
            Dismiss
          </ActionSheetItem>
        </ActionSheet>
      </div>
    </SwipeableCard>
  )
}
```

Note: "Save for later" is currently a no-op toast. Real save behavior is out of scope (no backend support exists today; spec doesn't require it for Plan B). The menu entry stays so the affordance is discoverable; backend save is a follow-up if desired.

- [ ] **Step 4: Run, expect 8 PASS**

If a test fails because `aria-label="More actions"` isn't found, double-check the `IconButton aria-label` matches the test selector. The default test runs the kebab via the action menu's "Dismiss" entry; the swipe test uses `fireEvent.pointer*` to simulate the gesture directly.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/feed/MatchCard.tsx frontend/src/components/feed/MatchCard.test.tsx
git commit -m "feat(frontend/feed): MatchCard component

Whole card is a real <Card as=rrlink> anchor → /matches/:id (preserves
long-press, middle-click). Swipe-left dismisses via SwipeableCard
primitive. Kebab IconButton in corner opens ActionSheet (Save / Open /
Dismiss) — far enough from the natural tap zone to avoid mis-taps."
```

After commit: 137 tests (129 + 8).

---

## Task 6: ProfileCompletenessCard

**Files:**
- Create: `frontend/src/components/feed/ProfileCompletenessCard.tsx`
- Create: `frontend/src/components/feed/ProfileCompletenessCard.test.tsx`

The card surfaces gates the first sync. Four states: setup, ready, paused, healthy/hidden. Healthy returns null.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { ProfileCompletenessCard } from './ProfileCompletenessCard'
import type { Profile } from '../../api/client'

function fullProfile(over: Partial<Profile> = {}): Profile {
  return {
    id: 'p-1',
    full_name: 'Maks',
    email: 'm@x.com',
    phone: null,
    linkedin_url: null,
    github_url: null,
    portfolio_url: null,
    base_resume_md: 'resume content',
    target_roles: ['Backend'],
    target_locations: ['Berlin'],
    remote_ok: true,
    seniority: 'senior',
    search_keywords: ['python'],
    search_active: true,
    search_expires_at: null,
    target_company_slugs: { greenhouse: ['stripe'] },
    skills: [],
    work_experiences: [],
    ...over,
  }
}

function renderCard(profile: Profile) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>
          <ProfileCompletenessCard profile={profile} />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('ProfileCompletenessCard', () => {
  it('renders nothing when profile is healthy + search active', () => {
    const { container } = renderCard(fullProfile())
    expect(container.firstChild).toBeNull()
  })

  it('renders setup state when resume missing', () => {
    renderCard(fullProfile({ base_resume_md: null }))
    expect(screen.getByText(/set up your search/i)).toBeInTheDocument()
    expect(screen.getByText(/resume/i)).toBeInTheDocument()
  })

  it('renders setup state when target_roles is empty', () => {
    renderCard(fullProfile({ target_roles: [] }))
    expect(screen.getByText(/target roles/i)).toBeInTheDocument()
  })

  it('renders the paused state when search_active=false', () => {
    renderCard(fullProfile({ search_active: false }))
    expect(screen.getByText(/search is paused/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /resume search/i })).toBeInTheDocument()
  })

  it('clicking Resume search calls toggleSearch(true)', async () => {
    let body: unknown = null
    server.use(
      http.patch('/api/profile/search', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ search_active: true, search_expires_at: null })
      }),
    )
    const user = userEvent.setup()
    renderCard(fullProfile({ search_active: false }))
    await user.click(screen.getByRole('button', { name: /resume search/i }))
    await waitFor(() => expect(body).toEqual({ search_active: true }))
  })

  it('shows "Tell coach" CTA when in setup state', () => {
    renderCard(fullProfile({ target_locations: [] }))
    expect(screen.getAllByText(/tell coach/i).length).toBeGreaterThan(0)
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

```tsx
import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Profile } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

interface CheckItem {
  id: string
  label: string
  done: boolean
  promptSlug: string
}

function checks(profile: Profile): CheckItem[] {
  return [
    { id: 'resume',    label: 'Resume',         done: !!profile.base_resume_md,        promptSlug: 'set_resume' },
    { id: 'roles',     label: 'Target roles',   done: profile.target_roles.length > 0, promptSlug: 'set_roles' },
    { id: 'locations', label: 'Locations',      done: profile.target_locations.length > 0 || !!profile.remote_ok, promptSlug: 'set_locations' },
    { id: 'keywords',  label: 'Search keywords',done: profile.search_keywords.length > 0, promptSlug: 'set_keywords' },
  ]
}

export function ProfileCompletenessCard({ profile }: { profile: Profile }) {
  const qc = useQueryClient()
  const { show } = useToast()

  const items = useMemo(() => checks(profile), [profile])
  const allDone = items.every((c) => c.done)
  const paused = profile.search_active === false

  const toggle = useMutation({
    mutationFn: (active: boolean) => api.toggleSearch(active),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['profile'] }),
    onError: (e) => show((e as Error)?.message ?? 'Could not update search', 'error'),
  })

  // Healthy + active → render nothing
  if (allDone && !paused) return null

  // Paused state
  if (paused) {
    return (
      <div className="mb-4 p-4 bg-warning/5 border border-warning/30 rounded-lg-token">
        <p className="text-sm font-semibold text-text mb-1">Search is paused</p>
        <p className="text-xs text-muted mb-3">We won't surface new matches while paused.</p>
        <Button size="sm" pending={toggle.isPending} onClick={() => toggle.mutate(true)}>
          Resume search
        </Button>
      </div>
    )
  }

  // Setup state (or ready state — distinguished by allDone)
  return (
    <div className="mb-4 p-4 bg-surface border border-border rounded-lg-token">
      <p className="text-sm font-semibold text-text mb-3">Set up your search</p>
      <ul className="space-y-2 text-sm">
        {items.map((c) => (
          <li key={c.id} className="flex items-center justify-between">
            <span className={c.done ? 'text-muted line-through' : 'text-text'}>
              <span className={`inline-block w-4 mr-2 ${c.done ? 'text-success' : 'text-subtle'}`}>
                {c.done ? '✓' : '○'}
              </span>
              {c.label}
            </span>
            {!c.done && (
              <Link
                to={`/?coach=1&prompt=${c.promptSlug}`}
                className="text-xs text-accent font-semibold px-2 py-1 rounded-md-token hover:bg-accent/10"
              >
                Tell coach →
              </Link>
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-subtle mt-3 pt-3 border-t border-border">
        {allDone
          ? 'Profile ready — your search will start automatically.'
          : 'Search will start automatically when these are set.'}
      </p>
    </div>
  )
}
```

- [ ] **Step 4: Run, expect 6 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/feed/ProfileCompletenessCard.tsx frontend/src/components/feed/ProfileCompletenessCard.test.tsx
git commit -m "feat(frontend/feed): ProfileCompletenessCard

Three rendered states: setup (checklist + 'Tell coach' deep links),
paused (Resume search button), healthy (returns null). Items derive
from profile.base_resume_md, target_roles, target_locations|remote_ok,
search_keywords. Coach drawer wiring (?coach=1) lands in Plan C; the
links are still well-formed URLs."
```

After commit: 143 tests (137 + 6).

---

## Task 7: Rewrite Matches.tsx as the new Feed page

**Files:**
- Modify: `frontend/src/pages/Matches.tsx` (full rewrite)
- Modify: `frontend/src/pages/Matches.test.tsx` (full rewrite)

The old file uses the old MatchCard, SyncStatusChip, InvalidSlugsNotice, and inline status filtering. New version composes the new components, adds status-driven filtering and a counts derivation.

- [ ] **Step 1: Replace test file**

Replace `frontend/src/pages/Matches.test.tsx` with:

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { ToastProvider } from '../components/ui/Toast'
import Matches from './Matches'
import type { Application, Profile } from '../api/client'

function makeApp(id: string, status: string, score = 0.8): Application {
  return {
    id, status, generation_status: 'none',
    match_score: score, match_summary: null, match_rationale: null,
    match_strengths: [], match_gaps: [],
    created_at: new Date().toISOString(),
    applied_at: null,
    job: { id: `job-${id}`, title: `Job ${id}`, company_name: 'Co', location: null,
           workplace_type: null, salary: null, contract_type: null,
           description_md: null, apply_url: 'https://x.com', posted_at: null },
  }
}

function fullProfile(): Profile {
  return {
    id: 'p-1', full_name: null, email: null, phone: null,
    linkedin_url: null, github_url: null, portfolio_url: null,
    base_resume_md: 'resume', target_roles: ['Backend'], target_locations: ['Berlin'],
    remote_ok: true, seniority: 'senior', search_keywords: ['python'],
    search_active: true, search_expires_at: null,
    target_company_slugs: { greenhouse: ['stripe'] },
    skills: [], work_experiences: [],
  }
}

function renderFeed(initialEntry = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Matches />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('Feed (Matches page)', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/profile', () => HttpResponse.json(fullProfile())),
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle', slugs_total: 0, slugs_pending: 0, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
  })

  it('renders skeleton while applications are loading', async () => {
    server.use(
      http.get('/api/applications', async () => {
        await new Promise((r) => setTimeout(r, 50))
        return HttpResponse.json([])
      }),
    )
    renderFeed()
    expect(screen.getAllByTestId('skel-card').length).toBeGreaterThan(0)
  })

  it('lists pending matches by default', async () => {
    server.use(
      http.get('/api/applications', () => HttpResponse.json([
        makeApp('1', 'pending_review'),
        makeApp('2', 'pending_review'),
      ])),
    )
    renderFeed()
    await waitFor(() => expect(screen.getByText('Job 1')).toBeInTheDocument())
    expect(screen.getByText('Job 2')).toBeInTheDocument()
  })

  it('filters by ?status=applied via the URL', async () => {
    server.use(
      http.get('/api/applications', ({ request }) => {
        const url = new URL(request.url)
        const s = url.searchParams.get('status')
        if (s === 'applied') return HttpResponse.json([makeApp('A', 'applied')])
        return HttpResponse.json([])
      }),
    )
    renderFeed('/?status=applied')
    await waitFor(() => expect(screen.getByText('Job A')).toBeInTheDocument())
  })

  it('shows the EmptyState when status is pending and list is empty', async () => {
    server.use(http.get('/api/applications', () => HttpResponse.json([])))
    renderFeed()
    await waitFor(() => expect(screen.getByText(/caught up/i)).toBeInTheDocument())
  })
})
```

- [ ] **Step 2: Run, expect FAIL (component still old; tests changed)**

```bash
cd frontend && npx vitest run src/pages/Matches.test.tsx
```

Expected: failures around new component selectors / new layout. Most existing assertions don't apply.

- [ ] **Step 3: Replace Matches.tsx**

Replace `frontend/src/pages/Matches.tsx` with:

```tsx
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, Application } from '../api/client'
import { useStatusFilter } from '../lib/useStatusFilter'
import { StatusChips, StatusCounts } from '../components/feed/StatusChips'
import { SyncRow } from '../components/feed/SyncRow'
import { MatchCard } from '../components/feed/MatchCard'
import { ProfileCompletenessCard } from '../components/feed/ProfileCompletenessCard'
import { SkeletonCard } from '../components/ui/Skeleton'
import { EmptyState } from '../components/ui/EmptyState'
import { Button } from '../components/ui/Button'

const SERVER_STATUS_BY_FILTER = {
  pending: 'pending_review',
  applied: 'applied',
  dismissed: 'dismissed',
} as const

function deriveCounts(byStatus: Partial<Record<'pending' | 'applied' | 'dismissed', Application[]>>): StatusCounts {
  return {
    pending:   byStatus.pending?.length ?? 0,
    applied:   byStatus.applied?.length ?? 0,
    dismissed: byStatus.dismissed?.length ?? 0,
  }
}

export default function Matches() {
  const { status } = useStatusFilter()

  const { data: profile } = useQuery({ queryKey: ['profile'], queryFn: api.getProfile })

  const apps = useQuery({
    queryKey: ['applications', status],
    queryFn: () => api.listApplications({ status: SERVER_STATUS_BY_FILTER[status] }),
    refetchInterval: 30_000,
  })

  // For the chip counts, fetch each status. Three small list fetches; cheap given app caps.
  const pendingQ   = useQuery({ queryKey: ['applications', 'pending'],   queryFn: () => api.listApplications({ status: 'pending_review' }), enabled: status !== 'pending'   })
  const appliedQ   = useQuery({ queryKey: ['applications', 'applied'],   queryFn: () => api.listApplications({ status: 'applied' }),        enabled: status !== 'applied'   })
  const dismissedQ = useQuery({ queryKey: ['applications', 'dismissed'], queryFn: () => api.listApplications({ status: 'dismissed' }),     enabled: status !== 'dismissed' })

  const counts = useMemo(() => deriveCounts({
    pending:   status === 'pending'   ? apps.data : pendingQ.data,
    applied:   status === 'applied'   ? apps.data : appliedQ.data,
    dismissed: status === 'dismissed' ? apps.data : dismissedQ.data,
  }), [status, apps.data, pendingQ.data, appliedQ.data, dismissedQ.data])

  return (
    <div>
      {profile && <ProfileCompletenessCard profile={profile} />}

      <div className="sticky top-14 z-10 -mx-4 px-4 py-3 bg-bg/90 backdrop-blur border-b border-border">
        <div className="flex items-center justify-between gap-3 mb-2">
          <StatusChips counts={counts} />
        </div>
        <SyncRow />
      </div>

      <div className="mt-4">
        {apps.isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div key={i} data-testid="skel-card"><SkeletonCard /></div>
            ))}
          </div>
        ) : !apps.data?.length ? (
          <EmptyState
            title={status === 'pending' ? 'Caught up' : `No ${status} matches`}
            description={status === 'pending'
              ? 'We\'ll surface new matches as boards refresh.'
              : `Nothing in your ${status} list yet.`}
            action={status === 'pending'
              ? <Button size="sm" variant="secondary" onClick={() => window.scrollTo({ top: 0 })}>Sync now</Button>
              : undefined}
          />
        ) : (
          <div className="space-y-2">
            {apps.data.map((app) => <MatchCard key={app.id} app={app} />)}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run, expect 4 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Matches.tsx frontend/src/pages/Matches.test.tsx
git commit -m "feat(frontend/pages): rewrite Matches.tsx as new Feed page

Composes ProfileCompletenessCard + StatusChips + SyncRow + MatchCard.
URL-driven status filter. Cheap counts via three parallel list queries
(disabled for the active filter to avoid double-fetch). Sticky chip+sync
row underneath the AppShell header. EmptyState replaces the old
hand-coded empty messages. Skeleton loading visible.

Old SyncStatusChip + InvalidSlugsNotice + old MatchCard imports gone;
those files become dead code (deleted in next task)."
```

After commit: ~147 tests (143 + 4 new feed tests). Old MatchCard.test.tsx (5 tests) still passes since the file still exists; total currently 147.

---

## Task 8: Delete dead components (old MatchCard + SyncStatusChip)

**Files:**
- Delete: `frontend/src/components/MatchCard.tsx`
- Delete: `frontend/src/components/MatchCard.test.tsx`
- Delete: `frontend/src/components/SyncStatusChip.tsx`
- Delete: `frontend/src/components/SyncStatusChip.test.tsx` (if it exists)

These are unused after Task 7. `InvalidSlugsNotice.tsx` is NOT deleted here — it'll move to the Settings page in Plan C and stays in place until then (it's currently unimported but harmless).

- [ ] **Step 1: Verify no remaining imports**

```bash
cd frontend
grep -rE "from .*components/MatchCard'" src/ || echo "no imports"
grep -rE "from .*components/SyncStatusChip'" src/ || echo "no imports"
```

Expected: both grep commands print "no imports".

- [ ] **Step 2: Delete the files**

```bash
rm src/components/MatchCard.tsx
rm src/components/MatchCard.test.tsx
rm src/components/SyncStatusChip.tsx
[ -f src/components/SyncStatusChip.test.tsx ] && rm src/components/SyncStatusChip.test.tsx
```

- [ ] **Step 3: Confirm tests still pass**

```bash
npm run test
```

Expected: pass count drops by however many tests the deleted files contained (5 from MatchCard test, possibly more from SyncStatusChip if a test existed). Final pass count should still be greater than the Task 0 baseline of 109.

- [ ] **Step 4: Type check**

```bash
npx tsc --noEmit
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src/components/
git commit -m "chore(frontend): delete dead components (old MatchCard, SyncStatusChip)

Both lost their sole consumer (Matches.tsx) in the rewrite. No
remaining imports anywhere in src/. InvalidSlugsNotice stays — Plan C
moves it into the new Settings page."
```

---

## Task 9: MatchHero component

**Files:**
- Create: `frontend/src/components/match-detail/MatchHero.tsx`
- Create: `frontend/src/components/match-detail/MatchHero.test.tsx`

Hero block: company (small / muted), title (large / bold), meta line (mono / subtle, gracefully shrinks).

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MatchHero } from './MatchHero'

const job = {
  id: 'j', title: 'Senior Backend Engineer', company_name: 'Acme', location: 'Berlin',
  workplace_type: 'hybrid', salary: '€100k', contract_type: null,
  description_md: null, apply_url: '#', posted_at: '2026-05-01',
}

describe('MatchHero', () => {
  it('renders title, company, and meta line', () => {
    render(<MatchHero job={job} />)
    expect(screen.getByRole('heading', { name: 'Senior Backend Engineer' })).toBeInTheDocument()
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText(/berlin/i)).toBeInTheDocument()
    expect(screen.getByText(/hybrid/i)).toBeInTheDocument()
    expect(screen.getByText(/€100k/)).toBeInTheDocument()
  })

  it('omits absent meta fields gracefully (no empty separators)', () => {
    render(<MatchHero job={{ ...job, location: null, workplace_type: null, salary: null }} />)
    expect(screen.queryByText(/·\s*·/)).not.toBeInTheDocument()
  })

  it('shows the relative posted age', () => {
    render(<MatchHero job={job} />)
    expect(screen.getByText(/posted/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

```tsx
import { Job } from '../../api/client'

function relativePosted(iso: string | null): string | null {
  if (!iso) return null
  const ms = Date.now() - new Date(iso).getTime()
  const d = Math.floor(ms / 86_400_000)
  if (d <= 0) return 'posted today'
  if (d === 1) return 'posted 1d ago'
  if (d < 30) return `posted ${d}d ago`
  return `posted ${new Date(iso).toLocaleDateString()}`
}

export function MatchHero({ job }: { job: Job }) {
  const meta = [job.location, job.workplace_type, job.salary, relativePosted(job.posted_at)]
    .filter(Boolean)
    .join(' · ')
  return (
    <header className="mb-6">
      <p className="text-sm text-muted">{job.company_name}</p>
      <h1 className="text-2xl font-bold tracking-tight text-text mt-0.5">{job.title}</h1>
      {meta && <p className="text-xs text-subtle font-mono mt-2">{meta}</p>}
    </header>
  )
}
```

- [ ] **Step 4: Run, expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/MatchHero.tsx frontend/src/components/match-detail/MatchHero.test.tsx
git commit -m "feat(frontend/match-detail): MatchHero component

Hero block. Title gets text-2xl/700, company is small/muted, meta line
in mono. Relative posted age formatted inline; absent fields drop out
gracefully so no empty '· ·' lingers."
```

After commit: ~145 tests (counting after Task 8's deletions).

---

## Task 10: MatchAnalysis component

**Files:**
- Create: `frontend/src/components/match-detail/MatchAnalysis.tsx`
- Create: `frontend/src/components/match-detail/MatchAnalysis.test.tsx`

The accent-bordered surface that shows score, summary, and full strengths/gaps lists.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MatchAnalysis } from './MatchAnalysis'

describe('MatchAnalysis', () => {
  it('renders score, summary, and full strengths/gaps lists', () => {
    render(<MatchAnalysis
      score={0.87} summary="Strong fit on Go"
      strengths={['Go', 'Postgres', 'Distributed systems']}
      gaps={['No public ML', 'No public k8s']}
    />)
    expect(screen.getByText('87% match')).toBeInTheDocument()
    expect(screen.getByText('Strong fit on Go')).toBeInTheDocument()
    expect(screen.getByText('Go')).toBeInTheDocument()
    expect(screen.getByText('Postgres')).toBeInTheDocument()
    expect(screen.getByText('Distributed systems')).toBeInTheDocument()
    expect(screen.getByText('No public ML')).toBeInTheDocument()
    expect(screen.getByText('No public k8s')).toBeInTheDocument()
  })

  it('returns null when score is null', () => {
    const { container } = render(<MatchAnalysis score={null} summary={null} strengths={[]} gaps={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders just score + summary when strengths and gaps are empty', () => {
    render(<MatchAnalysis score={0.8} summary="ok" strengths={[]} gaps={[]} />)
    expect(screen.getByText('80% match')).toBeInTheDocument()
    expect(screen.queryByText(/strengths/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/gaps/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

```tsx
import { ScoreBadge } from '../feed/ScoreBadge'

export interface MatchAnalysisProps {
  score: number | null
  summary: string | null
  strengths: string[]
  gaps: string[]
}

export function MatchAnalysis({ score, summary, strengths, gaps }: MatchAnalysisProps) {
  if (score == null) return null
  const hasLists = strengths.length > 0 || gaps.length > 0
  return (
    <section className="mb-6 bg-surface-2 border border-border border-l-4 border-l-accent rounded-lg-token p-4">
      <div className="flex items-center gap-2 mb-2">
        <ScoreBadge score={score} />
      </div>
      {summary && <p className="text-sm text-muted leading-relaxed">{summary}</p>}
      {hasLists && (
        <>
          <hr className="my-3 border-border" />
          <div className="grid md:grid-cols-2 gap-4">
            {strengths.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-success mb-1">Strengths</p>
                <ul className="text-sm text-muted space-y-0.5">
                  {strengths.map((s, i) => <li key={i}>— {s}</li>)}
                </ul>
              </div>
            )}
            {gaps.length > 0 && (
              <div>
                <p className="text-xs font-bold uppercase tracking-wider text-warning mb-1">Gaps</p>
                <ul className="text-sm text-muted space-y-0.5">
                  {gaps.map((g, i) => <li key={i}>— {g}</li>)}
                </ul>
              </div>
            )}
          </div>
        </>
      )}
    </section>
  )
}
```

- [ ] **Step 4: Run, expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/MatchAnalysis.tsx frontend/src/components/match-detail/MatchAnalysis.test.tsx
git commit -m "feat(frontend/match-detail): MatchAnalysis component

Score + summary + full strengths/gaps lists on a distinct surface
(accent left-border) that reads as the agent's verdict. Two-column
on md, stacked on mobile. No truncation per spec."
```

---

## Task 11: JobDescription component

**Files:**
- Create: `frontend/src/components/match-detail/JobDescription.tsx`
- Create: `frontend/src/components/match-detail/JobDescription.test.tsx`

Renders `description_md` inline at full width (no expander, no `max-h`).

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { JobDescription } from './JobDescription'

describe('JobDescription', () => {
  it('renders the description block when content present', () => {
    render(<JobDescription content="Acme is hiring engineers." />)
    expect(screen.getByText('Acme is hiring engineers.')).toBeInTheDocument()
    expect(screen.getByText(/job description/i)).toBeInTheDocument()
  })

  it('returns null when content is null', () => {
    const { container } = render(<JobDescription content={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('returns null on empty / whitespace-only content', () => {
    const { container } = render(<JobDescription content="   " />)
    expect(container.firstChild).toBeNull()
  })

  it('preserves whitespace via whitespace-pre-wrap (no expander)', () => {
    render(<JobDescription content="line one\n\nline two" />)
    const pre = screen.getByText(/line one/, { exact: false })
    expect(pre.className).toMatch(/whitespace-pre-wrap/)
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

```tsx
export function JobDescription({ content }: { content: string | null }) {
  if (!content || !content.trim()) return null
  return (
    <section className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="flex-1 h-px bg-border" />
        <span className="text-xs uppercase tracking-wider font-bold text-muted">Job description</span>
        <span className="flex-1 h-px bg-border" />
      </div>
      <pre className="whitespace-pre-wrap font-sans text-sm text-text leading-relaxed">{content}</pre>
    </section>
  )
}
```

(No markdown rendering library is added — `whitespace-pre-wrap` preserves line breaks, which is sufficient for the JSON-from-Greenhouse content shape today. Real markdown rendering is a follow-up if needed.)

- [ ] **Step 4: Run, expect 4 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/JobDescription.tsx frontend/src/components/match-detail/JobDescription.test.tsx
git commit -m "feat(frontend/match-detail): JobDescription component

Inline full-width description (no expander, no max-h clipping per spec).
whitespace-pre-wrap preserves line breaks. Section header with divider.
Real markdown rendering deferred — content today is fine as preformatted."
```

---

## Task 12: CoverLetterEditor component

**Files:**
- Create: `frontend/src/components/match-detail/CoverLetterEditor.tsx`
- Create: `frontend/src/components/match-detail/CoverLetterEditor.test.tsx`

Editor with toolbar (provenance + Save / PDF / Regenerate kebab) and the textarea. Generate flow handles the empty case + retry-on-fail.

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { CoverLetterEditor } from './CoverLetterEditor'
import type { Document } from '../../api/client'

function withQuery(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

const baseDoc: Document = {
  id: 'd1', doc_type: 'cover_letter', content_md: 'Dear team,\n\nMy pitch.',
  structured_content: null, has_edits: false, generation_model: 'gemini-2.5-pro',
  created_at: new Date().toISOString(),
}

describe('CoverLetterEditor', () => {
  it('renders the Generate button when no document is present', () => {
    render(withQuery(<CoverLetterEditor appId="app-1" doc={null} status="none" />))
    expect(screen.getByRole('button', { name: /generate cover letter/i })).toBeInTheDocument()
  })

  it('renders the editor textarea when a document is present', () => {
    render(withQuery(<CoverLetterEditor appId="app-1" doc={baseDoc} status="ready" />))
    expect(screen.getByLabelText(/cover letter/i)).toHaveValue(baseDoc.content_md)
  })

  it('clicking Generate calls POST /api/applications/:id/cover-letter', async () => {
    let called = false
    server.use(
      http.post('/api/applications/app-1/cover-letter', () => {
        called = true
        return HttpResponse.json({
          id: 'd1', doc_type: 'cover_letter', content_md: 'gen', generation_model: 'gemini-2.5-pro',
          created_at: new Date().toISOString(),
        })
      }),
    )
    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={null} status="none" />))
    await user.click(screen.getByRole('button', { name: /generate cover letter/i }))
    await waitFor(() => expect(called).toBe(true))
  })

  it('shows an error toast on generation failure', async () => {
    server.use(
      http.post('/api/applications/app-1/cover-letter', () =>
        HttpResponse.json({ detail: 'rate limited' }, { status: 429 })),
    )
    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={null} status="none" />))
    await user.click(screen.getByRole('button', { name: /generate cover letter/i }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/rate limited/i))
  })

  it('Save edits PATCHes the document', async () => {
    let body: unknown = null
    server.use(
      http.patch('/api/applications/app-1/documents/d1', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ id: 'd1', saved: true })
      }),
    )
    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={baseDoc} status="ready" />))
    await user.clear(screen.getByLabelText(/cover letter/i))
    await user.type(screen.getByLabelText(/cover letter/i), 'edited')
    await user.click(screen.getByRole('button', { name: /save edits/i }))
    await waitFor(() => expect(body).toMatchObject({ user_edited_md: 'edited' }))
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

```tsx
import { useState, useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, Document } from '../../api/client'
import { Button } from '../ui/Button'
import { TextArea } from '../ui/TextArea'
import { useToast } from '../ui/Toast'

export interface CoverLetterEditorProps {
  appId: string
  doc: Document | null
  status: string
}

export function CoverLetterEditor({ appId, doc, status }: CoverLetterEditorProps) {
  const qc = useQueryClient()
  const { show } = useToast()
  const [content, setContent] = useState(doc?.content_md ?? '')

  // Reset when the upstream doc changes (e.g. after generation succeeds).
  useEffect(() => { setContent(doc?.content_md ?? '') }, [doc?.content_md])

  const generate = useMutation({
    mutationFn: () => api.generateCoverLetter(appId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', appId] }),
    onError: (e) => show((e as Error)?.message ?? 'Generation failed', 'error'),
  })

  const save = useMutation({
    mutationFn: () => api.updateDocument(appId, doc!.id, { user_edited_md: content }),
    onSuccess: () => {
      show('Saved', 'success')
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not save edits', 'error'),
  })

  if (!doc) {
    return (
      <section className="mb-6">
        <div className="flex items-center gap-3 mb-2">
          <span className="flex-1 h-px bg-border" />
          <span className="text-xs uppercase tracking-wider font-bold text-muted">Cover letter</span>
          <span className="flex-1 h-px bg-border" />
        </div>
        <Button
          pending={generate.isPending}
          onClick={() => generate.mutate()}
        >
          {generate.isPending ? 'Generating cover letter…' : 'Generate cover letter'}
        </Button>
        <p className="text-xs text-subtle mt-2">Takes about 30 seconds.</p>
        {status === 'failed' && !generate.isPending && (
          <p className="text-xs text-danger mt-2">Last attempt failed. Tap to try again.</p>
        )}
      </section>
    )
  }

  const dirty = content !== doc.content_md
  return (
    <section className="mb-6">
      <div className="flex items-center gap-3 mb-2">
        <span className="flex-1 h-px bg-border" />
        <span className="text-xs uppercase tracking-wider font-bold text-muted">Cover letter</span>
        <span className="flex-1 h-px bg-border" />
      </div>
      <div className="flex items-center justify-between mb-2 text-xs text-subtle">
        <span>{doc.has_edits ? 'Edited' : 'AI-generated'} · {doc.generation_model ?? ''}</span>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save edits'}
          </Button>
          <a
            href={api.downloadPdf(doc.id)} target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center px-3 py-1.5 rounded-md-token text-sm text-muted hover:text-text hover:bg-surface min-h-[32px]"
          >
            PDF ↓
          </a>
        </div>
      </div>
      <TextArea
        label="Cover letter"
        value={content}
        rows={12}
        onChange={(e) => setContent(e.target.value)}
        spellCheck={false}
      />
    </section>
  )
}
```

- [ ] **Step 4: Run, expect 5 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/CoverLetterEditor.tsx frontend/src/components/match-detail/CoverLetterEditor.test.tsx
git commit -m "feat(frontend/match-detail): CoverLetterEditor component

Empty: Generate button + helper. Generated: TextArea (auto-resize via
primitive) + Save edits / PDF download in toolbar. Toast on save success
and generation failure. Regenerate is deferred — kebab confirm in a
follow-up if needed."
```

---

## Task 13: StickyActions component

**Files:**
- Create: `frontend/src/components/match-detail/StickyActions.tsx`
- Create: `frontend/src/components/match-detail/StickyActions.test.tsx`

Mobile-only sticky bottom bar. "Open posting" optimistically marks applied AND opens the URL. Once applied, bar shows "✓ Applied · Open posting again ↗".

- [ ] **Step 1: Failing test**

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { StickyActions } from './StickyActions'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>{node}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('StickyActions', () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('renders Skip + Open posting when status is pending_review', () => {
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /skip/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open posting/i })).toBeInTheDocument()
  })

  it('clicking Open posting opens the URL AND POSTs mark-applied', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied', applied_at: new Date().toISOString() })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('link', { name: /open posting/i }))
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await waitFor(() => expect(posted).toBe(true))
  })

  it('renders the applied state when status="applied"', () => {
    render(withCtx(<StickyActions appId="a1" status="applied" applyUrl="https://x.com/" />))
    expect(screen.getByText(/applied/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open posting again/i })).toBeInTheDocument()
  })

  it('shows error toast if mark-applied fails', async () => {
    server.use(
      http.post('/api/applications/a1/mark-applied', () =>
        HttpResponse.json({ detail: 'no' }, { status: 500 })),
    )
    const user = userEvent.setup()
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('link', { name: /open posting/i }))
    await waitFor(() => expect(screen.getByRole('status').className).toMatch(/border-l-danger/))
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

- [ ] **Step 3: Implement**

```tsx
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { Button } from '../ui/Button'
import { useToast } from '../ui/Toast'

export interface StickyActionsProps {
  appId: string
  status: string
  applyUrl: string
}

export function StickyActions({ appId, status, applyUrl }: StickyActionsProps) {
  const qc = useQueryClient()
  const { show } = useToast()

  const markApplied = useMutation({
    mutationFn: () => api.markApplied(appId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', appId] }),
    onError: (e) => show((e as Error)?.message ?? "Couldn't mark as applied — try again", 'error'),
  })

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(appId, 'dismissed'),
    onSuccess: () => {
      show('Dismissed', 'info')
      qc.invalidateQueries({ queryKey: ['application', appId] })
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  function onOpenAndMark(e: React.MouseEvent) {
    e.preventDefault()
    window.open(applyUrl, '_blank', 'noopener')
    if (status === 'pending_review') markApplied.mutate()
  }

  if (status === 'applied') {
    return (
      <div className="md:hidden fixed bottom-0 inset-x-0 bg-success/10 border-t border-success/30 px-4 py-3 flex items-center justify-between">
        <span className="text-sm text-success font-semibold">✓ Applied</span>
        <a
          href={applyUrl} onClick={onOpenAndMark}
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
        onClick={() => dismiss.mutate()}
      >
        ⏷ Skip
      </Button>
      <a
        href={applyUrl} onClick={onOpenAndMark}
        className="flex-1 inline-flex items-center justify-center bg-accent text-accent-fg font-semibold rounded-md-token px-4 py-2.5 min-h-[40px]"
      >
        Open posting ↗
      </a>
    </div>
  )
}
```

- [ ] **Step 4: Run, expect 4 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/match-detail/StickyActions.tsx frontend/src/components/match-detail/StickyActions.test.tsx
git commit -m "feat(frontend/match-detail): StickyActions component

Mobile-only bottom bar. Skip (ghost) + Open posting (primary). Open
posting fires markApplied optimistically AND opens the URL in a new
tab. Once applied, bar collapses to '✓ Applied · Open posting again ↗'.
safe-area-inset-bottom keeps it above the iOS keyboard."
```

---

## Task 14: Rewrite ApplicationReview.tsx as the new MatchDetail page

**Files:**
- Modify: `frontend/src/pages/ApplicationReview.tsx` (full rewrite)
- Create: `frontend/src/pages/ApplicationReview.test.tsx` (no existing test — confirm by listing the directory; if the test file does exist, replace its contents)

Composes the four match-detail components plus a header with back nav and kebab.

- [ ] **Step 1: Confirm test file state, write test**

```bash
cd frontend
[ -f src/pages/ApplicationReview.test.tsx ] && echo "exists" || echo "missing"
```

Either way, write/replace `frontend/src/pages/ApplicationReview.test.tsx`:

```tsx
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { server } from '../test/server'
import { ToastProvider } from '../components/ui/Toast'
import ApplicationReview from './ApplicationReview'
import type { ApplicationDetail } from '../api/client'

function detail(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 'a1', status: 'pending_review', generation_status: 'none',
    match_score: 0.87, match_summary: 'Strong fit',
    match_rationale: null, match_strengths: ['Go'], match_gaps: ['No ML'],
    created_at: new Date().toISOString(),
    applied_at: null,
    job: {
      id: 'j', title: 'Senior Backend Engineer', company_name: 'Acme',
      location: 'Berlin', workplace_type: 'hybrid', salary: '€100k',
      contract_type: null, description_md: 'Acme is hiring.', apply_url: 'https://x.com/',
      posted_at: null,
    },
    documents: [],
    generation_attempts: 0,
    ...over,
  }
}

function renderAt(initialEntry: string, mock: ApplicationDetail) {
  server.use(http.get('/api/applications/a1', () => HttpResponse.json(mock)))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/matches/:id" element={<ApplicationReview />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('Match detail (ApplicationReview)', () => {
  it('renders hero, match analysis, description, and editor placeholder', async () => {
    renderAt('/matches/a1', detail())
    await waitFor(() => expect(screen.getByRole('heading', { name: /senior backend engineer/i })).toBeInTheDocument())
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText('87% match')).toBeInTheDocument()
    expect(screen.getByText(/acme is hiring/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /generate cover letter/i })).toBeInTheDocument()
  })

  it('renders the cover letter editor when a document exists', async () => {
    renderAt('/matches/a1', detail({
      generation_status: 'ready',
      documents: [{
        id: 'd1', doc_type: 'cover_letter', content_md: 'Dear team,', structured_content: null,
        has_edits: false, generation_model: 'gemini-2.5-pro', created_at: new Date().toISOString(),
      }],
    }))
    await waitFor(() => expect(screen.getByLabelText(/cover letter/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /save edits/i })).toBeInTheDocument()
  })

  it('shows the loading state before the fetch completes', () => {
    server.use(http.get('/api/applications/a1', async () => {
      await new Promise((r) => setTimeout(r, 50))
      return HttpResponse.json(detail())
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <MemoryRouter initialEntries={['/matches/a1']}>
            <Routes>
              <Route path="/matches/:id" element={<ApplicationReview />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>
    )
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run, expect FAIL**

```bash
npx vitest run src/pages/ApplicationReview.test.tsx
```

- [ ] **Step 3: Replace ApplicationReview.tsx**

```tsx
import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { IconButton } from '../components/ui/IconButton'
import { ActionSheet, ActionSheetItem } from '../components/ui/ActionSheet'
import { Kebab, Close } from '../components/ui/icons'
import { useToast } from '../components/ui/Toast'
import { MatchHero } from '../components/match-detail/MatchHero'
import { MatchAnalysis } from '../components/match-detail/MatchAnalysis'
import { JobDescription } from '../components/match-detail/JobDescription'
import { CoverLetterEditor } from '../components/match-detail/CoverLetterEditor'
import { StickyActions } from '../components/match-detail/StickyActions'

export default function ApplicationReview() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { show } = useToast()
  const [menuOpen, setMenuOpen] = useState(false)

  const { data: app, isLoading } = useQuery({
    queryKey: ['application', id],
    queryFn: () => api.getApplication(id!),
    enabled: !!id,
  })

  const dismiss = useMutation({
    mutationFn: () => api.reviewApplication(id!, 'dismissed'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['applications'] })
      navigate(-1)
      show('Dismissed', 'info')
    },
    onError: (e) => show((e as Error)?.message ?? 'Could not dismiss', 'error'),
  })

  const moveBackToPending = useMutation({
    // Sends status back to pending_review via the same review endpoint.
    // The PATCH endpoint accepts 'dismissed' or 'applied' today; for 'move
    // back to pending' we need the API to allow 'pending_review'. If it
    // doesn't, this mutation will surface an error toast — and Plan C / a
    // backend follow-up will add the case. Documenting here so it's clear.
    mutationFn: async () => {
      // Cast to any-string until the API client's type is widened; this
      // is intentional and the mutation is a follow-up if the endpoint rejects.
      return api.reviewApplication(id!, 'pending_review' as 'dismissed' | 'applied')
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['application', id] }),
    onError: (e) => show((e as Error)?.message ?? 'Backend does not yet allow un-applying', 'error'),
  })

  if (isLoading || !app) {
    return <div className="flex items-center justify-center h-48 text-muted">Loading…</div>
  }
  if (!app.job) {
    return <div className="text-muted">Job data missing.</div>
  }

  const cover = app.documents?.find((d) => d.doc_type === 'cover_letter') ?? null

  return (
    <article className="pb-24 md:pb-6">
      <header className="sticky top-14 z-10 -mx-4 px-4 py-2 bg-bg/90 backdrop-blur border-b border-border flex items-center justify-between">
        <IconButton aria-label="Back" onClick={() => navigate(-1)}>
          <Close className="w-4 h-4" />
        </IconButton>
        <IconButton aria-label="More actions" onClick={() => setMenuOpen(true)}>
          <Kebab className="w-4 h-4" />
        </IconButton>
      </header>

      <div className="mt-4">
        <MatchHero job={app.job} />
        <MatchAnalysis
          score={app.match_score}
          summary={app.match_summary}
          strengths={app.match_strengths}
          gaps={app.match_gaps}
        />
        <JobDescription content={app.job.description_md} />
        <CoverLetterEditor appId={app.id} doc={cover} status={app.generation_status} />
      </div>

      <StickyActions
        appId={app.id}
        status={app.status}
        applyUrl={app.job.apply_url}
      />

      <ActionSheet open={menuOpen} onClose={() => setMenuOpen(false)} title="Match actions">
        <ActionSheetItem onClick={() => { setMenuOpen(false); window.open(app.job!.apply_url, '_blank', 'noopener') }}>
          Open original posting ↗
        </ActionSheetItem>
        {app.status === 'applied' && (
          <ActionSheetItem onClick={() => { setMenuOpen(false); moveBackToPending.mutate() }}>
            Move back to pending
          </ActionSheetItem>
        )}
        {app.status !== 'dismissed' && (
          <ActionSheetItem intent="danger" onClick={() => { setMenuOpen(false); dismiss.mutate() }}>
            Dismiss
          </ActionSheetItem>
        )}
      </ActionSheet>
    </article>
  )
}
```

- [ ] **Step 4: Run, expect 3 PASS**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ApplicationReview.tsx frontend/src/pages/ApplicationReview.test.tsx
git commit -m "feat(frontend/pages): rewrite ApplicationReview as new Match Detail page

Composes MatchHero + MatchAnalysis + JobDescription + CoverLetterEditor
+ StickyActions. Sticky header has Back + kebab. Description renders
inline (no expander, no max-h). 'Move back to pending' action surfaces
when status=applied — backend currently only accepts dismissed/applied;
mutation will error toast until a backend follow-up extends the API."
```

---

## Task 15: Update App.tsx — add `/` Feed alias

**Files:**
- Modify: `frontend/src/App.tsx`

Plan A added `/login`. Plan B adds `/` as the new Feed home. Old `/matches` keeps working as the same Feed page (so existing bookmarks don't break). `/applied` and `/profile` aliases stay for now (Plan D removes them).

- [ ] **Step 1: Replace App.tsx**

```tsx
import { Routes, Route } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { ToastProvider } from './components/ui/Toast'
import { AppShell } from './components/AppShell'
import BudgetBanner from './components/BudgetBanner'
import RequireAuth from './components/RequireAuth'
import Landing from './pages/Landing'
import AuthCallback from './pages/AuthCallback'
import Matches from './pages/Matches'
import ApplicationReview from './pages/ApplicationReview'
import Applied from './pages/Applied'
import Onboarding from './pages/Onboarding'

function ShellRoutes() {
  return (
    <>
      <BudgetBanner />
      <AppShell>
        <Routes>
          <Route path="/" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/login" element={<Landing />} />
          <Route path="/auth/callback" element={<AuthCallback />} />
          <Route path="/matches" element={<RequireAuth><Matches /></RequireAuth>} />
          <Route path="/matches/:id" element={<RequireAuth><ApplicationReview /></RequireAuth>} />
          <Route path="/applied" element={<RequireAuth><Applied /></RequireAuth>} />
          <Route path="/profile" element={<RequireAuth><Onboarding /></RequireAuth>} />
          <Route path="/settings" element={<RequireAuth><Onboarding /></RequireAuth>} />
        </Routes>
      </AppShell>
    </>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <ToastProvider>
        <ShellRoutes />
      </ToastProvider>
    </AuthProvider>
  )
}
```

Important change from Plan A: `/` is now `RequireAuth → Matches` (the Feed). The old behavior had `/` → Landing for unauthenticated users — but with `RequireAuth`, unauth users at `/` get redirected to `/login`. `/login` continues to render Landing. This matches the spec's "Auth gating" rule: "RequireAuth wraps `/`, `/matches/:id`, `/settings`."

- [ ] **Step 2: Fix RequireAuth's redirect target**

The existing `RequireAuth.tsx` (verified during plan writing) redirects unauthenticated users to `/`. With `/` now auth-gated under `RequireAuth`, that creates an infinite redirect loop. Fix by editing `frontend/src/components/RequireAuth.tsx`:

Replace the file with:

```tsx
import { Navigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen text-muted">
        Loading...
      </div>
    )
  }
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}
```

Two changes vs current: `to="/"` → `to="/login"` (avoids the loop), and `text-gray-500` → `text-muted` (use the new token).

- [ ] **Step 3: Run full test suite**

```bash
npm run test
```

Expected: all tests pass. Counts depend on Task 8 deletions but should be strictly greater than the Task 0 baseline.

- [ ] **Step 4: Type check + build**

```bash
npx tsc --noEmit && npm run build
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/RequireAuth.tsx
git commit -m "feat(frontend): make / the Feed (auth-gated); keep /matches as alias

/ now routes to Matches (the Feed) wrapped in RequireAuth.
Unauthenticated users redirect to /login (Landing).
/matches still works as a backwards-compat alias — folded into / in
Plan D cleanup."
```

---

## Task 16: Final verification + PR

**Files:** none (CI + push + PR)

- [ ] **Step 1: Full unit test run**

```bash
cd frontend && npm run test
```

Expected: all tests pass. Note final count.

- [ ] **Step 2: Type check**

```bash
npx tsc --noEmit
```

- [ ] **Step 3: Production build**

```bash
npm run build
```

- [ ] **Step 4: Run e2e tests**

This requires Postgres + the FastAPI backend. Start them:

```bash
cd /Users/panibrat/dev/job-application-agent
docker compose up -d db
until docker compose exec db pg_isready -U postgres > /dev/null 2>&1; do sleep 1; done
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  make migrate ARGS="upgrade head"
DATABASE_URL="postgresql+asyncpg://jobagent:jobagent@localhost:5432/jobagent" \
  GOOGLE_API_KEY=test-key ENVIRONMENT=development \
  npm --prefix frontend run test:e2e
```

Expected: 15/15 e2e tests pass. The existing `matching.spec.ts` tests assertions like "matches page shows seeded jobs" — these should still work since:
- Page lives at `/matches` (route alias kept)
- Match cards still display title/company text the e2e looks for

If a spec fails:
- Selectors that targeted the OLD MatchCard's "Review →" link will now find an actual `<a>` (the whole card). Update to `getByRole('link', { name: /<job title>/i })`.
- "Dismiss" button no longer exists per-card; tests that asserted the small Dismiss button must use the kebab menu: `getByRole('button', { name: /more actions/i })` then `getByText(/dismiss/i)` in the action sheet.
- ApplicationReview tests that opened the "Job Details" expander no longer need the click — description is inline.

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feat/ui-pages
gh pr create --title "feat(frontend): UX redesign Plan B — Feed + Match detail" --body "$(cat <<'EOF'
## Summary
Per spec at \`docs/superpowers/specs/2026-05-06-frontend-ux-redesign-design.md\` sections 4–5. Builds on Plan A's design system (PR #93).

**Feed page:**
- New \`/\` (auth-gated) route shows the Feed; \`/matches\` kept as alias.
- Profile-completeness card (setup / paused / hidden states) drives first-sync.
- URL-driven status filter chips (Pending / Applied / Dismissed); URL is \`?status=\`.
- New \`MatchCard\`: whole card is a real anchor, swipe-left-to-dismiss with kebab fallback, undo-toast on dismiss.
- New \`SyncRow\`: replaces \`SyncStatusChip\` + plain button; live state in the button copy + vague-interval idle hint.
- EmptyState + SkeletonCard for empty/loading states.
- Old \`MatchCard.tsx\` and \`SyncStatusChip.tsx\` deleted (no remaining consumers).

**Match detail page:**
- Hero (title 24px / company small / mono meta line).
- MatchAnalysis surface (accent left-border, full strengths/gaps lists).
- JobDescription: inline, full-width, no expander, no max-h.
- CoverLetterEditor: TextArea primitive, Save / PDF, Generate flow with toasts.
- StickyActions (mobile): Skip + Open posting; Open posting fires \`markApplied\` optimistically AND opens the URL. Once applied, bar shows '✓ Applied · Open posting again ↗'. Move-back-to-pending in the kebab.

**Out of scope (Plans C / D):**
- Settings page, Coach drawer wiring, SSE meta marker.
- Analytics events table + ingest + SQL views.
- Cleanup of \`/applied\`, \`/profile\` route aliases and \`Applied.tsx\` / \`Onboarding.tsx\`.

## Test plan
- [ ] CI green (\`npm run test\`, \`tsc --noEmit\`, \`npm run build\`)
- [ ] CI green for e2e (matching / application-review / onboarding / auth-and-nav specs)
- [ ] Manual: chip switching changes URL and list contents
- [ ] Manual: swipe-to-dismiss + undo toast on mobile width
- [ ] Manual: Open posting on mobile detail marks the application applied AND opens the URL
- [ ] Manual: Resume search button works when paused

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Confirm CI passes on the PR**

Wait for CI to complete. If a job fails, fix on this branch (NEW commits, never amend) and push again.

---

## Self-Review Checklist

- [ ] Spec coverage:
  - **Section 4 — Feed:** profile-completeness card (3 of 4 states implemented; "Ready / first-sync" state simplified into Setup-when-allDone — render `'Profile ready — your search will start automatically'`. The dedicated `Start search` button is deferred since auto-sync is the default).
  - Status chips, sticky chip+sync row, sync button + live state — covered (Tasks 2/3/7).
  - MatchCard — covered (Task 5). Whole-card link, kebab fallback, swipe, undo.
  - Empty/loading states — covered (Task 7).
  - Pull-to-refresh — **NOT covered**. Deferred to a follow-up; not blocking shippable value. Documented as out-of-scope below.
  - InvalidSlugsNotice move — NOT covered. Stays in `components/` until Plan C moves it to Settings.
  - **Section 5 — Match detail:** Hero, MatchAnalysis, JobDescription, CoverLetterEditor, StickyActions all covered.
  - "Move back to pending" — covered as a kebab item (Task 14), but backend may not accept; toast surfaces error gracefully.
  - "Read more" fade for very long descriptions — NOT covered (default behavior is full-render; was a "if absurdly long" caveat in spec).
- [ ] Placeholder scan: clean.
- [ ] Type consistency: `Application`, `Profile`, `Document`, `Job`, `SyncStatus`, `StatusCounts`, `ScoreBadge`, `GenerationBadge`, `useStatusFilter`, `Card as="rrlink"` — all consistent with their definitions.
- [ ] AppShell URL contract from Plan A: Coach button writes `?coach=1` (Plan A); ProfileCompletenessCard's "Tell coach" links use the same param + `&prompt=<slug>` (this plan, Task 6). Plan C will add a Drawer reader for the param.

## Out of scope for Plan B (carried forward to Plan C / D)

- **Settings page** — `/settings` still aliases to `Onboarding.tsx` (Plan A's transitional alias).
- **Coach drawer** — `?coach=1` is written by the AppShell (Plan A) and the ProfileCompletenessCard CTAs (this plan), but there's no reader yet. Plan C adds the Drawer + chat content.
- **SSE meta marker for agent profile mutations** — Plan C, ties into Coach drawer behavior.
- **Analytics events table + ingest + SQL views** — Plan D.
- **Pull-to-refresh** on the Feed — deferred. Cheap follow-up if mobile testing flags it as missing.
- **InvalidSlugsNotice relocation** — Plan C (moves into Settings page).
- **"Read more" inline fade** for very long descriptions — only matters for outliers.
- **Backend support for `pending_review` in PATCH `/api/applications/:id`** — needed for "Move back to pending" to actually work; gracefully error-toasts today.
- **Cleanup of `/applied`, `/profile` routes and `Applied.tsx`/`Onboarding.tsx`** — Plan D.

End of Plan B.
