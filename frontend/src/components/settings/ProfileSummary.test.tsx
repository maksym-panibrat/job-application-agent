import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ProfileSummary } from './ProfileSummary'
import type { Profile } from '../../api/client'

function fullProfile(over: Partial<Profile> = {}): Profile {
  return {
    id: 'p-1', full_name: 'Maks', email: 'm@x.com', phone: null,
    linkedin_url: null, github_url: null, portfolio_url: null,
    base_resume_md: 'r', target_roles: ['Backend', 'Platform'],
    target_locations: ['Berlin', 'Remote-EU'], remote_ok: true,
    seniority: 'senior', search_keywords: ['python'], search_active: true,
    search_expires_at: null,
    subscription: null,
    entitlements: { paid_access: false, search_auto_pause: true },
    limits: { followed_companies: 5 },
    target_companies: [{ id: 'co-1', canonical_name: 'Stripe' }],
    skills: [
      { id: 's1', name: 'Go', category: null, proficiency: null, years: 5 },
      { id: 's2', name: 'Postgres', category: null, proficiency: null, years: 7 },
    ],
    work_experiences: [
      { id: 'w1', company: 'Acme', title: 'Eng', start_date: '2020-01-01',
        end_date: null, description_md: null, technologies: [] },
    ],
    ...over,
  }
}

describe('ProfileSummary', () => {
  it('renders roles, locations, salary line, skills count, experience count', () => {
    render(
      <MemoryRouter>
        <ProfileSummary profile={fullProfile()} />
      </MemoryRouter>
    )
    expect(screen.getByText(/backend/i)).toBeInTheDocument()
    expect(screen.getByText(/berlin/i)).toBeInTheDocument()
    expect(screen.getByText(/2 skills/i)).toBeInTheDocument()
    expect(screen.getByText(/1 experience/i)).toBeInTheDocument()
  })

  it('Open Chat CTA links to ?chat=1&prompt=change_profile', () => {
    render(
      <MemoryRouter>
        <ProfileSummary profile={fullProfile()} />
      </MemoryRouter>
    )
    const link = screen.getByRole('link', { name: /open chat/i })
    expect(link.getAttribute('href')).toMatch(/chat=1/)
    expect(link.getAttribute('href')).toMatch(/prompt=change_profile/)
  })
})
