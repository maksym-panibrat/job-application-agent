import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { ToastProvider } from './ui/Toast'

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@example.com' },
    token: 'fake',
    loading: false,
    signOut: vi.fn(),
  }),
  AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

vi.mock('../lib/track', () => ({
  track: vi.fn(),
}))

import { AppShell } from './AppShell'
import { CoachDrawer } from './coach/CoachDrawer'

function renderShell(pathname = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[pathname]}>
          <AppShell>
            <p>page body</p>
          </AppShell>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

function renderShellWithCoach(pathname = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[pathname]}>
          <AppShell>
            <p>page body</p>
          </AppShell>
          <CoachDrawer />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

const idleStatus = {
  state: 'idle' as const,
  slugs_total: 0,
  slugs_pending: 0,
  matches_pending: 0,
  last_sync_requested_at: null,
  last_sync_completed_at: null,
  last_sync_summary: null,
  invalid_slugs: [],
}

describe('AppShell (desktop)', () => {
  beforeEach(() => {
    server.use(http.get('/api/sync/status', () => HttpResponse.json(idleStatus)))
  })

  it('renders children inside <main>', () => {
    renderShell()
    expect(screen.getByText('page body')).toBeInTheDocument()
  })

  it('renders the brand link → /', () => {
    renderShell('/anywhere')
    const brand = screen.getByText('Job Agent')
    expect(brand.closest('a')).toHaveAttribute('href', '/')
  })

  it('renders Sync, Settings, Coach, Sign-out controls (desktop bar)', () => {
    renderShell()
    expect(screen.getByRole('button', { name: /sync now/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /settings/i })).toHaveAttribute('href', '/settings')
    expect(screen.getByRole('button', { name: /coach/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign out/i })).toBeInTheDocument()
  })

  it('renders the hamburger button (visible on mobile; rendered at all widths)', () => {
    renderShell()
    expect(screen.getByRole('button', { name: /open menu/i })).toBeInTheDocument()
  })

  it('opens the Coach drawer when Coach is clicked', async () => {
    const user = userEvent.setup()
    renderShellWithCoach('/')

    await user.click(screen.getByRole('button', { name: /coach/i }))

    expect(screen.getByRole('dialog', { name: 'Coach' })).toBeInTheDocument()
  })
})

describe('AppShell sync (header button)', () => {
  beforeEach(() => {
    server.use(http.get('/api/sync/status', () => HttpResponse.json(idleStatus)))
  })

  it('clicking the header sync button POSTs /api/jobs/sync and shows a toast', async () => {
    let posted = false
    server.use(
      http.post('/api/jobs/sync', () => {
        posted = true
        return HttpResponse.json({ status: 'queued', queued_slugs: ['stripe'], matched_now: 2, seeded_defaults: false })
      }),
    )
    const user = userEvent.setup()
    renderShell()

    await user.click(screen.getByRole('button', { name: /sync now/i }))
    await waitFor(() => expect(posted).toBe(true))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/searching/i)
    )
  })

  it('reflects live sync state in the button aria-label and disables it', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        ...idleStatus, state: 'syncing', slugs_total: 12, slugs_pending: 5,
      })),
    )
    renderShell()

    const btn = await screen.findByRole('button', { name: /searching 7 of 12 boards/i })
    expect(btn).toBeDisabled()
  })

  it('reflects matching state ("Scoring N jobs…")', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        ...idleStatus, state: 'matching', matches_pending: 8,
      })),
    )
    renderShell()
    expect(await screen.findByRole('button', { name: /scoring 8 jobs/i })).toBeInTheDocument()
  })
})

describe('AppShell sync (mobile menu)', () => {
  beforeEach(() => {
    server.use(http.get('/api/sync/status', () => HttpResponse.json(idleStatus)))
  })

  it('opening the hamburger reveals a Sync entry that triggers /api/jobs/sync', async () => {
    let posted = false
    server.use(
      http.post('/api/jobs/sync', () => {
        posted = true
        return HttpResponse.json({ status: 'queued', queued_slugs: [], matched_now: 0, seeded_defaults: false })
      }),
    )
    const user = userEvent.setup()
    renderShell()

    await user.click(screen.getByRole('button', { name: /open menu/i }))
    // Two buttons match /sync/ inside menu (header + sheet) — pick the sheet entry
    const sheetItems = screen.getAllByRole('button', { name: /sync now/i })
    // The sheet entry is inside a [role=dialog]; the header one is not.
    const sheetSync = sheetItems.find((b) => b.closest('[role=dialog]'))
    expect(sheetSync).toBeDefined()
    await user.click(sheetSync!)
    await waitFor(() => expect(posted).toBe(true))
  })
})
