import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import BudgetBanner from './BudgetBanner'

describe('BudgetBanner', () => {
  it('renders nothing when budget is not exhausted', async () => {
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.queryByText(/AI features paused/)).not.toBeInTheDocument()
    })
  })

  it('renders banner when budget_exhausted is true', async () => {
    server.use(
      http.get('/api/status', () =>
        HttpResponse.json({ budget_exhausted: true, resumes_at: null })
      )
    )
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.getByText(/AI features paused until next month/)).toBeInTheDocument()
    })
  })

  it('renders the formatted resumes_at date when provided', async () => {
    server.use(
      http.get('/api/status', () =>
        HttpResponse.json({
          budget_exhausted: true,
          resumes_at: '2025-05-01T00:00:00Z',
        })
      )
    )
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.getByText(/AI features paused until/)).toBeInTheDocument()
    })
    expect(screen.queryByText(/next month/)).not.toBeInTheDocument()
  })

  it('renders "next month" when resumes_at is null', async () => {
    server.use(
      http.get('/api/status', () =>
        HttpResponse.json({ budget_exhausted: true, resumes_at: null })
      )
    )
    render(<BudgetBanner />)
    await waitFor(() => {
      expect(screen.getByText(/next month/)).toBeInTheDocument()
    })
  })
})
