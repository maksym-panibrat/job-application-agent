import { describe, it, expect } from 'vitest'
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
    target_companies: [{ id: 'co-1', canonical_name: 'Stripe' }],
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

  it('does not require optional search keywords for a healthy profile', () => {
    const { container } = renderCard(fullProfile({ search_keywords: [] }))
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

  it('renders setup state when no companies are followed', () => {
    renderCard(fullProfile({ target_companies: [] }))
    expect(screen.getByText(/followed companies/i)).toBeInTheDocument()
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

  it('shows "Open chat" CTA when in setup state', () => {
    renderCard(fullProfile({ target_locations: [], remote_ok: false }))
    expect(screen.getAllByText(/open chat/i).length).toBeGreaterThan(0)
  })
})
