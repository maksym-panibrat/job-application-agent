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
    user_interest: null,
    created_at: new Date().toISOString(),
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
      ats_type: null,
      supports_api_apply: false,
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

  it('thumbs-up button sends PATCH with interested', async () => {
    const user = userEvent.setup()
    let patchedBody: unknown
    server.use(
      http.patch('/api/applications/:id/interest', async ({ request }) => {
        patchedBody = await request.json()
        return HttpResponse.json(null)
      })
    )
    renderCard(makeApp())
    await user.click(screen.getByLabelText('Mark as interested'))
    await waitFor(() => {
      expect(patchedBody).toEqual({ interest: 'interested' })
    })
  })

  it('thumbs-up again toggles interest back to null', async () => {
    const user = userEvent.setup()
    const bodies: unknown[] = []
    server.use(
      http.patch('/api/applications/:id/interest', async ({ request }) => {
        bodies.push(await request.json())
        return HttpResponse.json(null)
      })
    )
    renderCard(makeApp({ user_interest: 'interested' }))
    await user.click(screen.getByLabelText('Mark as interested'))
    await waitFor(() => {
      expect(bodies[bodies.length - 1]).toEqual({ interest: null })
    })
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
