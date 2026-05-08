import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import type { ReactNode } from 'react'
import { useApplyAction } from './useApplyAction'

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('useApplyAction', () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  beforeEach(() => {
    openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
  })

  it('onOpen opens URL in a new tab and POSTs mark-applied when status is pending_review', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied', applied_at: new Date().toISOString() })
      }),
    )
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'pending_review', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    await act(async () => { result.current.onOpen() })
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    await waitFor(() => expect(posted).toBe(true))
  })

  it('onOpen opens URL but does NOT POST mark-applied when status is applied', async () => {
    let posted = false
    server.use(
      http.post('/api/applications/a1/mark-applied', () => {
        posted = true
        return HttpResponse.json({ id: 'a1', status: 'applied' })
      }),
    )
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'applied', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    await act(async () => { result.current.onOpen() })
    expect(openSpy).toHaveBeenCalledWith('https://x.com/', '_blank', 'noopener')
    // Give the mutation a tick — should not fire.
    await new Promise((r) => setTimeout(r, 20))
    expect(posted).toBe(false)
  })

  it('exposes isApplied=true when status is applied', () => {
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'applied', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    expect(result.current.isApplied).toBe(true)
  })

  it('exposes isApplied=false when status is pending_review', () => {
    const { result } = renderHook(
      () => useApplyAction({ appId: 'a1', status: 'pending_review', applyUrl: 'https://x.com/' }),
      { wrapper },
    )
    expect(result.current.isApplied).toBe(false)
  })
})
