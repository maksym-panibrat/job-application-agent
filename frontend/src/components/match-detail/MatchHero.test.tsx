import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MatchHero } from './MatchHero'

const job = {
  id: 'j', title: 'Senior Backend Engineer', company_name: 'Acme', location: 'Berlin',
  workplace_type: 'hybrid', salary: '€100k', contract_type: null,
  description: null, apply_url: '#', posted_at: '2026-05-01',
}

describe('MatchHero', () => {
  it('renders title, company, and meta line', () => {
    render(<MatchHero job={job} />)
    expect(screen.getByRole('heading', { name: 'Senior Backend Engineer' })).toBeInTheDocument()
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText(/berlin/i)).toBeInTheDocument()
    expect(screen.getByText(/hybrid/i)).toBeInTheDocument()
    expect(screen.getByText(/€100k/)).toBeInTheDocument()
  })

  it('omits absent meta fields gracefully (no empty separators)', () => {
    render(<MatchHero job={{ ...job, location: null, workplace_type: null, salary: null }} />)
    expect(screen.queryByText(/·\s*·/)).not.toBeInTheDocument()
  })

  it('shows the relative posted age', () => {
    render(<MatchHero job={job} />)
    expect(screen.getByText(/posted/i)).toBeInTheDocument()
  })
})
