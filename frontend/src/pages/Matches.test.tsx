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
