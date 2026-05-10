import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { ToastProvider } from '../ui/Toast'
import { Chat } from './Chat'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

function sseStreamResponse(body: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body))
      controller.close()
    },
  })
  return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

describe('Chat', () => {
  it('renders with composer and resume upload affordances', () => {
    render(withCtx(<Chat />))
    expect(screen.getByPlaceholderText(/type your/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /resume/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^send$/i })).toBeInTheDocument()
  })

  it('prefills composer with initialPrompt (does not auto-send)', () => {
    render(withCtx(<Chat initialPrompt="What roles am I targeting?" />))
    const input = screen.getByPlaceholderText(/type your/i) as HTMLInputElement
    expect(input.value).toBe('What roles am I targeting?')
  })

  it('appends user + assistant message bubbles when sent', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (url === '/api/chat/messages') {
        return Promise.resolve(sseStreamResponse('data: {"content":"hi back"}\n\ndata: [DONE]\n\n'))
      }
      return originalFetch(url)
    }) as typeof fetch

    const user = userEvent.setup()
    render(withCtx(<Chat />))
    await user.type(screen.getByPlaceholderText(/type your/i), 'hello')
    await user.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(screen.getByText('hello')).toBeInTheDocument())
    await waitFor(() => expect(screen.getByText('hi back')).toBeInTheDocument())
    globalThis.fetch = originalFetch
  })

  it('renders a Search now button when the agent reply emits profile_mutated meta', async () => {
    const originalFetch = globalThis.fetch
    let syncCalled = false
    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/chat/messages') {
        const body =
          'data: {"content":"updated"}\n\n' +
          'event: meta\ndata: {"profile_mutated": true}\n\n' +
          'data: [DONE]\n\n'
        return Promise.resolve(sseStreamResponse(body))
      }
      if (url === '/api/jobs/sync' && init?.method === 'POST') {
        syncCalled = true
        return Promise.resolve(new Response(JSON.stringify({
          status: 'queued', queued_slugs: [], matched_now: 0,
        }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      }
      return originalFetch(url)
    }) as typeof fetch

    const user = userEvent.setup()
    render(withCtx(<Chat />))
    await user.type(screen.getByPlaceholderText(/type your/i), 'set roles')
    await user.click(screen.getByRole('button', { name: /^send$/i }))
    await waitFor(() => expect(screen.getByText('updated')).toBeInTheDocument())
    const cta = await screen.findByRole('button', { name: /search now/i })
    await user.click(cta)
    await waitFor(() => expect(syncCalled).toBe(true))
    globalThis.fetch = originalFetch
  })
})
