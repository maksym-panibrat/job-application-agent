import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../test/server'
import { MatchCard } from './MatchCard'
import type { Application } from '../api/client'

function makeApp(overrides: Partial<Application> = {}): Application {
  return {
    id: 'app-1',
    status: 'pending_review',
    generation_status: 'none',
    match_score: 0.85,
    match_rationale: 'Strong Python background matches the role requirements.',
    match_strengths: ['Python', 'FastAPI'],
    match_gaps: ['Go experience'],
    created_at: new Date().toISOString(),
    applied_at: null,
    job: {
      id: 'job-1',
      title: 'Python Engineer',
      company_name: 'Acme Corp',
      location: 'Remote',
      workplace_type: 'remote',
      salary: '$120k',
      contract_type: 'full-time',
      description_md: 'A great role.',
      apply_url: 'https://example.com/apply',
      posted_at: null,
    },
    ...overrides,
  }
}

function renderCard(app: Application) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <MatchCard app={app} />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('MatchCard', () => {
  it('renders job title, company, and match score badge', () => {
    renderCard(makeApp())
    expect(screen.getByText('Python Engineer')).toBeInTheDocument()
    expect(screen.getByText('Acme Corp')).toBeInTheDocument()
    expect(screen.getByText('85% match')).toBeInTheDocument()
  })

  it('renders strengths and gaps', () => {
    renderCard(makeApp())
    expect(screen.getByText('Python, FastAPI')).toBeInTheDocument()
    expect(screen.getByText(/Go experience/)).toBeInTheDocument()
  })

  it('review link points to /matches/app-1', () => {
    renderCard(makeApp())
    const link = screen.getByText('Review →')
    expect(link.closest('a')).toHaveAttribute('href', '/matches/app-1')
  })

  it('dismiss button sends PATCH with dismissed status', async () => {
    const user = userEvent.setup()
    let patchedBody: unknown
    server.use(
      http.patch('/api/applications/app-1', async ({ request }) => {
        patchedBody = await request.json()
        return HttpResponse.json({ id: 'app-1', status: 'dismissed' })
      })
    )
    renderCard(makeApp())
    await user.click(screen.getByText('Dismiss'))
    await waitFor(() => {
      expect(patchedBody).toEqual({ status: 'dismissed' })
    })
  })
})
