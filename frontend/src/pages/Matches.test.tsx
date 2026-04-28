import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import Matches from './Matches'

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Matches />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Matches sync errors', () => {
  it('surfaces the daily-limit message when /api/jobs/sync returns 429', async () => {
    server.use(
      http.post('/api/jobs/sync', () =>
        HttpResponse.json(
          { detail: "Daily limit of 25 for 'manual_sync' reached. Try again tomorrow." },
          { status: 429 }
        )
      )
    )

    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: /sync jobs/i }))

    await waitFor(() => {
      expect(screen.getByText(/Daily limit of 25/i)).toBeInTheDocument()
      expect(screen.getByText(/try again tomorrow/i)).toBeInTheDocument()
    })
  })
})

describe('Matches dashboard states', () => {
  it("shows the empty-state CTA when there are no pending applications", async () => {
    // Default handler in test/server.ts already returns []
    renderPage()
    await waitFor(() => {
      expect(screen.getByText(/No matches yet/i)).toBeInTheDocument()
    })
  })

  it('renders MatchCard rows when /api/applications returns data', async () => {
    server.use(
      http.get('/api/applications', () =>
        HttpResponse.json([
          {
            id: 'app-stripe-1',
            status: 'pending_review',
            generation_status: 'none',
            match_score: 0.9,
            match_rationale: 'Strong Python + distributed systems match.',
            match_strengths: ['Python', 'Distributed systems'],
            match_gaps: [],
            created_at: new Date().toISOString(),
            applied_at: null,
            job: {
              id: 'job-1',
              title: 'Backend Engineer',
              company_name: 'Stripe',
              location: 'Remote',
              workplace_type: 'remote',
              salary: '$200k',
              contract_type: 'full-time',
              description_md: 'A great role.',
              apply_url: 'https://example.com/apply',
              posted_at: null,
            },
          },
        ])
      )
    )

    renderPage()
    await waitFor(() => {
      expect(screen.getByText('Backend Engineer')).toBeInTheDocument()
      expect(screen.getByText('Stripe')).toBeInTheDocument()
      expect(screen.getByText('90% match')).toBeInTheDocument()
    })
  })

  it('shows the success toast after a successful sync', async () => {
    server.use(
      http.post('/api/jobs/sync', () =>
        HttpResponse.json({ status: 'synced', new_jobs: 7, updated_jobs: 3, stale_jobs: 0 })
      )
    )

    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: /sync jobs/i }))

    await waitFor(() => {
      expect(screen.getByText(/Sync complete: 7 new jobs, 3 updated/i)).toBeInTheDocument()
    })
  })
})
