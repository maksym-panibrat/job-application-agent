import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { TargetSlugsSection } from './TargetSlugsSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('TargetSlugsSection', () => {
  it('renders existing greenhouse slugs as chips', () => {
    render(withCtx(<TargetSlugsSection slugs={{ greenhouse: ['stripe', 'vercel'] }} />))
    expect(screen.getByText('stripe')).toBeInTheDocument()
    expect(screen.getByText('vercel')).toBeInTheDocument()
  })

  it('removes a slug via the chip remove button', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/profile', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'p-1', updated: true })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<TargetSlugsSection slugs={{ greenhouse: ['stripe'] }} />))
    await user.click(screen.getByRole('button', { name: /remove stripe/i }))
    await waitFor(() => expect(patched).toMatchObject({
      target_company_slugs: { greenhouse: [] },
    }))
  })

  it('adds a slug via the +Add input', async () => {
    let patched: unknown = null
    server.use(
      http.patch('/api/profile', async ({ request }) => {
        patched = await request.json()
        return HttpResponse.json({ id: 'p-1', updated: true })
      }),
    )
    const user = userEvent.setup()
    render(withCtx(<TargetSlugsSection slugs={{ greenhouse: ['stripe'] }} />))
    const input = screen.getByPlaceholderText(/add greenhouse slug/i)
    await user.type(input, 'newco')
    await user.keyboard('{Enter}')
    await waitFor(() => expect(patched).toMatchObject({
      target_company_slugs: { greenhouse: ['stripe', 'newco'] },
    }))
  })
})
