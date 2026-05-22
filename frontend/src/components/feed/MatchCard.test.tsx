import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
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
    // Two "Dismiss" affordances render now: the kebab item and the trailing
    // swipe-action button. Assert >= 1 so this test stays meaningful.
    expect(screen.getAllByText(/dismiss/i).length).toBeGreaterThanOrEqual(1)
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
    // Both the kebab item and the trailing swipe-action have role=button with
    // name "Dismiss". Disambiguate by scoping to the action-sheet dialog.
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /dismiss/i }))
    await waitFor(() => expect(patched).toEqual({ status: 'dismissed' }))
    expect(screen.getByRole('status')).toHaveTextContent(/dismissed/i)
  })

  it('renders a trailing Dismiss swipe-action whose click triggers dismissal', async () => {
    // We migrated swipe to react-swipeable-list (Type.IOS, fullSwipe). Simulating
    // a real swipe in jsdom is brittle; the lib also exposes the action as a
    // clickable element so users can tap-to-confirm without a full swipe.
    // Asserting the click path covers both "tap on revealed action" and the
    // wire-up to the dismiss mutation.
    let patched: unknown = null
    server.use(
      http.patch('/api/applications/app-1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'app-1', status: 'dismissed' })
      }),
    )
    const user = userEvent.setup()
    renderCard(makeApp())
    const trailingDismiss = screen.getByRole('button', { name: /^dismiss$/i })
    await user.click(trailingDismiss)
    await waitFor(() => expect(patched).toEqual({ status: 'dismissed' }))
  })

  it('returns null when job is missing (defensive)', () => {
    const { container } = renderCard(makeApp({ job: null }))
    expect(container.firstChild).toBeNull()
  })

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

  it('does NOT render the trailing Dismiss swipe-action when status is dismissed', () => {
    renderCard(makeApp({ status: 'dismissed' }))
    // The only "Dismiss" affordance for a dismissed card lives inside the kebab
    // (which we open separately). There must be no inline button labelled
    // "Dismiss" in the rendered output — otherwise a tap could re-dismiss an
    // already-dismissed match.
    expect(screen.queryByRole('button', { name: /^dismiss$/i })).not.toBeInTheDocument()
  })
})
