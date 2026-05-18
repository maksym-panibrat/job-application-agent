import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { ToastProvider } from '../components/ui/Toast'
import Settings from './Settings'
import type { Profile } from '../api/client'

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 'u-1', email: 'maks@x.com' },
    token: 'fake', loading: false, signOut: vi.fn(),
  }),
}))

function fullProfile(): Profile {
  return {
    id: 'p-1', full_name: 'Maks', email: 'm@x.com', phone: null,
    linkedin_url: null, github_url: null, portfolio_url: null,
    base_resume_md: 'r', target_roles: ['Backend'], target_locations: ['Berlin'],
    remote_ok: true, seniority: 'senior',
    search_active: true, search_expires_at: null,
    target_companies: [{ id: 'co-1', canonical_name: 'Stripe' }],
    skills: [], work_experiences: [],
  }
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>
          <Settings />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('Settings page', () => {
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

  it('renders all sections after profile loads', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByText(/search active/i)).toBeInTheDocument())
    expect(screen.getByText(/resume on file/i)).toBeInTheDocument()
    expect(screen.getByText(/followed companies/i)).toBeInTheDocument()
    expect(screen.getByText(/account/i)).toBeInTheDocument()
    expect(screen.getByText(/profile/i)).toBeInTheDocument()
  })

  it('shows the loading state before profile resolves', () => {
    server.use(http.get('/api/profile', async () => {
      await new Promise((r) => setTimeout(r, 50))
      return HttpResponse.json(fullProfile())
    }))
    renderPage()
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })
})
