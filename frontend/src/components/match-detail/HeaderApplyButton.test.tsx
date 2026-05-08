import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { HeaderApplyButton } from './HeaderApplyButton'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('HeaderApplyButton', () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('renders nothing when status is dismissed', () => {
    const { container } = render(withCtx(
      <HeaderApplyButton appId="a1" status="dismissed" applyUrl="https://x.com/" />
    ))
    expect(container).toBeEmptyDOMElement()
  })

  it('renders "Open posting ↗" when status is pending_review', () => {
    render(withCtx(
      <HeaderApplyButton appId="a1" status="pending_review" applyUrl="https://x.com/" />
    ))
    expect(screen.getByRole('link', { name: /open posting/i })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /open posting again/i })).not.toBeInTheDocument()
  })

  it('renders "Open posting again ↗" when status is applied', () => {
    render(withCtx(
      <HeaderApplyButton appId="a1" status="applied" applyUrl="https://x.com/" />
    ))
    expect(screen.getByRole('link', { name: /open posting again/i })).toBeInTheDocument()
  })

  it('clicking the button opens the URL and POSTs mark-applied when pending_review', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied', applied_at: new Date().toISOString() })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(
      <HeaderApplyButton appId="a1" status="pending_review" applyUrl="https://x.com/" />
    ))
    await user.click(screen.getByRole('link', { name: /open posting/i }))
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await waitFor(() => expect(posted).toBe(true))
  })

  it('clicking the button opens the URL but does NOT POST mark-applied when applied', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied' })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(
      <HeaderApplyButton appId="a1" status="applied" applyUrl="https://x.com/" />
    ))
    await user.click(screen.getByRole('link', { name: /open posting again/i }))
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await new Promise((r) => setTimeout(r, 20))
    expect(posted).toBe(false)
  })

  it('is hidden on mobile via the hidden md:inline-flex utility', () => {
    render(withCtx(
      <HeaderApplyButton appId="a1" status="pending_review" applyUrl="https://x.com/" />
    ))
    const link = screen.getByRole('link', { name: /open posting/i })
    expect(link.className).toContain('hidden')
    expect(link.className).toContain('md:inline-flex')
  })
})
