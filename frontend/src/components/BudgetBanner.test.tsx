import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { vi } from 'vitest'
import { server } from '../test/server'
import BudgetBanner from './BudgetBanner'

function renderBudgetBanner() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <BudgetBanner />
    </QueryClientProvider>
  )
}

describe('BudgetBanner', () => {
  it('renders nothing when budget is not exhausted', async () => {
    renderBudgetBanner()
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
    renderBudgetBanner()
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
    renderBudgetBanner()
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
    renderBudgetBanner()
    await waitFor(() => {
      expect(screen.getByText(/next month/)).toBeInTheDocument()
    })
  })

  it('does not use a component-local minute interval', async () => {
    const intervalSpy = vi.spyOn(globalThis, 'setInterval')
    let calls = 0
    server.use(
      http.get('/api/status', () => {
        calls += 1
        return HttpResponse.json({ budget_exhausted: false, resumes_at: null })
      }),
    )
    renderBudgetBanner()
    await waitFor(() => expect(calls).toBe(1))

    expect(intervalSpy.mock.calls.some((call) => call[1] === 60_000)).toBe(false)
    intervalSpy.mockRestore()
  })
})
