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
           description_raw: null, description: null, apply_url: 'https://x.com', posted_at: null },
  }
}

function fullProfile(): Profile {
  return {
    id: 'p-1', full_name: null, email: null, phone: null,
    linkedin_url: null, github_url: null, portfolio_url: null,
    base_resume_md: 'resume', target_roles: ['Backend'], target_locations: ['Berlin'],
    remote_ok: true, seniority: 'senior', search_keywords: ['python'],
    search_active: true, search_expires_at: null,
    target_companies: [{ id: 'co-1', canonical_name: 'Stripe' }],
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
