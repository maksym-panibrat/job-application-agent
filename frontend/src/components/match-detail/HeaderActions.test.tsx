import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { HeaderActions } from './HeaderActions'

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

describe('HeaderActions (desktop inline actions)', () => {
  beforeEach(() => {
    vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('renders Dismiss + Apply for status=pending_review', () => {
    render(withCtx(<HeaderActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /^dismiss$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /apply/i })).toBeInTheDocument()
  })

  it('renders Unapply + Open again for status=applied', () => {
    render(withCtx(<HeaderActions appId="a1" status="applied" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /^unapply$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open again/i })).toBeInTheDocument()
  })

  it('renders Restore + Apply for status=dismissed (so users are not stuck on desktop)', () => {
    render(withCtx(<HeaderActions appId="a1" status="dismissed" applyUrl="https://x.com/" />))
    expect(screen.getByRole('button', { name: /^restore$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /apply/i })).toBeInTheDocument()
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
    render(withCtx(<HeaderActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('button', { name: /^dismiss$/i }))
    await waitFor(() => expect(patched).toEqual({ status: 'dismissed' }))
  })

  it('clicking Restore POSTs review=pending_review', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/applications/a1', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'a1', status: 'pending_review' })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<HeaderActions appId="a1" status="dismissed" applyUrl="https://x.com/" />))
    await user.click(screen.getByRole('button', { name: /^restore$/i }))
    await waitFor(() => expect(patched).toEqual({ status: 'pending_review' }))
  })

  it('is hidden on mobile (md:flex)', () => {
    const { container } = render(withCtx(<HeaderActions appId="a1" status="pending_review" applyUrl="https://x.com/" />))
    const wrapper = container.firstChild as HTMLElement
    expect(wrapper.className).toContain('hidden')
    expect(wrapper.className).toContain('md:flex')
  })
})
