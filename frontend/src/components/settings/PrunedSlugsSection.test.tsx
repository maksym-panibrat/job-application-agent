import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { PrunedSlugsSection } from './PrunedSlugsSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{node}</QueryClientProvider>)
}

describe('PrunedSlugsSection', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle', slugs_total: 0, slugs_pending: 0, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: ['defunct-co', 'gone-co'],
      })),
    )
  })

  it('renders nothing when there are no invalid slugs', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle', slugs_total: 0, slugs_pending: 0, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
    const { container } = withCtx(<PrunedSlugsSection />)
    await waitFor(() => expect(container.firstChild).toBeNull())
  })

  it('lists invalid slugs', async () => {
    withCtx(<PrunedSlugsSection />)
    await waitFor(() => expect(screen.getByText('defunct-co')).toBeInTheDocument())
    expect(screen.getByText('gone-co')).toBeInTheDocument()
  })

  it('per-slug dismiss removes a single chip from view', async () => {
    const user = userEvent.setup()
    withCtx(<PrunedSlugsSection />)
    await waitFor(() => expect(screen.getByText('defunct-co')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: /dismiss defunct-co/i }))
    expect(screen.queryByText('defunct-co')).not.toBeInTheDocument()
    expect(screen.getByText('gone-co')).toBeInTheDocument()
  })
})
