import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Matches from '../pages/Matches'

// Mock the API module
vi.mock('../api/client', () => ({
  api: {
    listApplications: vi.fn().mockResolvedValue([]),
    triggerSync: vi.fn(),
  },
}))

function Wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Matches page', () => {
  it('renders heading after load', async () => {
    render(<Matches />, { wrapper: Wrapper })
    await waitFor(() => {
      expect(screen.getByText('Job Matches')).toBeInTheDocument()
    })
  })

  it('renders sync button after load', async () => {
    render(<Matches />, { wrapper: Wrapper })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sync jobs/i })).toBeInTheDocument()
    })
  })

  it('shows empty state when no matches', async () => {
    render(<Matches />, { wrapper: Wrapper })
    await waitFor(() => {
      expect(screen.getByText(/no matches yet/i)).toBeInTheDocument()
    })
  })
})
