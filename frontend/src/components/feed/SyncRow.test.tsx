import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { SyncRow } from './SyncRow'

function renderRow() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <SyncRow />
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('SyncRow', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'idle',
        slugs_total: 0,
        slugs_pending: 0,
        matches_pending: 0,
        last_sync_requested_at: null,
        last_sync_completed_at: new Date().toISOString(),
        last_sync_summary: null,
        invalid_slugs: [],
      })),
    )
  })

  it('renders an idle "Sync now" button by default', async () => {
    renderRow()
    expect(await screen.findByRole('button', { name: /sync now/i })).toBeInTheDocument()
  })

  it('shows "Searching… N of M" when status is syncing', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'syncing', slugs_total: 12, slugs_pending: 5, matches_pending: 0,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
    renderRow()
    expect(await screen.findByText(/searching/i)).toBeInTheDocument()
    expect(await screen.findByText(/7 of 12/i)).toBeInTheDocument()
  })

  it('shows "Scoring N jobs…" when status is matching', async () => {
    server.use(
      http.get('/api/sync/status', () => HttpResponse.json({
        state: 'matching', slugs_total: 0, slugs_pending: 0, matches_pending: 8,
        last_sync_requested_at: null, last_sync_completed_at: null,
        last_sync_summary: null, invalid_slugs: [],
      })),
    )
    renderRow()
    expect(await screen.findByText(/scoring/i)).toBeInTheDocument()
    expect(await screen.findByText(/8 jobs/i)).toBeInTheDocument()
  })

  it('clicking the button triggers POST /api/jobs/sync and shows a success toast on success', async () => {
    server.use(
      http.post('/api/jobs/sync', () => HttpResponse.json({
        status: 'queued', queued_slugs: ['stripe', 'vercel'], matched_now: 5, seeded_defaults: false,
      })),
    )
    const user = userEvent.setup()
    renderRow()
    await user.click(await screen.findByRole('button', { name: /sync now/i }))
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/searching/i)
    )
  })

  it('shows a danger toast when sync fails', async () => {
    server.use(
      http.post('/api/jobs/sync', () => HttpResponse.json({ detail: 'rate limited' }, { status: 429 })),
    )
    const user = userEvent.setup()
    renderRow()
    await user.click(await screen.findByRole('button', { name: /sync now/i }))
    await waitFor(() =>
      expect(screen.getByRole('status').className).toMatch(/border-l-danger/)
    )
  })
})
