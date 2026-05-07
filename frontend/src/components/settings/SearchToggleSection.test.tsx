import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { SearchToggleSection } from './SearchToggleSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('SearchToggleSection', () => {
  it('renders active state with Pause button', () => {
    render(withCtx(<SearchToggleSection active expiresAt={null} />))
    expect(screen.getByText(/search active/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /pause/i })).toBeInTheDocument()
  })

  it('renders paused state with Resume button', () => {
    render(withCtx(<SearchToggleSection active={false} expiresAt={null} />))
    expect(screen.getByText(/search paused/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
  })

  it('shows expiry countdown when expiresAt is in the future', () => {
    const inThreeDays = new Date(Date.now() + 3 * 86_400_000).toISOString()
    render(withCtx(<SearchToggleSection active expiresAt={inThreeDays} />))
    expect(screen.getByText(/3 days/i)).toBeInTheDocument()
  })

  it('clicking Pause calls toggleSearch(false)', async () => {
    let body: unknown = null
    server.use(
      http.patch('/api/profile/search', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ search_active: false, search_expires_at: null })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<SearchToggleSection active expiresAt={null} />))
    await user.click(screen.getByRole('button', { name: /pause/i }))
    await waitFor(() => expect(body).toEqual({ search_active: false }))
  })
})
