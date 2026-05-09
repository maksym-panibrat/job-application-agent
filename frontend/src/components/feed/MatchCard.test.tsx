import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
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
      description_raw: '',
      description: null,
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
})
