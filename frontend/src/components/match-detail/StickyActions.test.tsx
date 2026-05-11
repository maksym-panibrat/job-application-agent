import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { StickyActions } from './StickyActions'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter>{node}</MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('StickyActions', () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('renders Back + Dismiss + Apply for status=pending_review', () => {
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /← back/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^dismiss$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /apply/i })).toBeInTheDocument()
    // The chevron icon prefix on the old "Skip" label must not be present.
    expect(screen.queryByText(/skip/i)).not.toBeInTheDocument()
  })

  it('clicking Apply opens the URL AND POSTs mark-applied', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied', applied_at: new Date().toISOString() })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('link', { name: /apply/i }))
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await waitFor(() => expect(posted).toBe(true))
  })

  it('renders Back + Unapply + Open again for status=applied', () => {
    render(withCtx(<StickyActions appId="a1" status="applied" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /← back/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^unapply$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open again/i })).toBeInTheDocument()
  })

  it('renders Back + Restore + Apply for status=dismissed', () => {
    render(withCtx(<StickyActions appId="a1" status="dismissed" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /← back/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^restore$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /apply/i })).toBeInTheDocument()
  })

  it('shows error toast if mark-applied fails', async () => {
    server.use(
      http.post('/api/applications/a1/mark-applied', () =>
        HttpResponse.json({ detail: 'no' }, { status: 500 })),
    )
    const user = userEvent.setup()
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('link', { name: /apply/i }))
    await waitFor(() => expect(screen.getByRole('status').className).toMatch(/border-l-danger/))
  })

  it('clicking Dismiss POSTs review=dismissed', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/applications/a1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'a1', status: 'dismissed' })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<StickyActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('button', { name: /^dismiss$/i }))
    await waitFor(() => expect(patched).toEqual({ status: 'dismissed' }))
  })
})
