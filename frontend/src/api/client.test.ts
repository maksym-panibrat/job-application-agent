import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api } from './client'

function mockFetch(status: number, body: unknown, headers?: Record<string, string>) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json', ...headers },
    })
  )
}

describe('api client', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    sessionStorage.clear()
  })

  describe('apiFetch error handling', () => {
    it('returns parsed JSON on 200', async () => {
      mockFetch(200, { id: '1', email: 'test@test.com' })
      const result = await api.getMe()
      expect(result).toEqual({ id: '1', email: 'test@test.com' })
    })

    it('throws on 401', async () => {
      mockFetch(401, { detail: 'Not authenticated' })
      await expect(api.getMe()).rejects.toThrow()
    })

    it('throws on 500', async () => {
      mockFetch(500, { detail: 'Internal server error' })
      await expect(api.listApplications()).rejects.toThrow()
    })

    it('includes Authorization header when token is in sessionStorage', async () => {
      sessionStorage.setItem('access_token', 'my-token')
      const spy = mockFetch(200, { id: '1', email: 'x@test.com' })
      await api.getMe()
      const calledHeaders = (spy.mock.calls[0][1] as RequestInit)?.headers as Record<string, string>
      expect(calledHeaders['Authorization']).toBe('Bearer my-token')
    })
  })

  describe('sendMessage SSE streaming', () => {
    it('calls onChunk for each data line with content', async () => {
      const encoder = new TextEncoder()
      const chunks = [
        'data: {"content": "Hello"}\n\n',
        'data: {"content": " world"}\n\n',
        'data: [DONE]\n\n',
      ]
      let i = 0
      const stream = new ReadableStream({
        pull(controller) {
          if (i < chunks.length) {
            controller.enqueue(encoder.encode(chunks[i++]))
          } else {
            controller.close()
          }
        },
      })

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
        new Response(stream, { status: 200 })
      )

      const received: string[] = []
      await api.sendMessage('hello', (chunk) => received.push(chunk))
      expect(received).toEqual(['Hello', ' world'])
    })

    it('ignores non-data SSE lines', async () => {
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            encoder.encode('event: message\ndata: {"content": "Hi"}\n\ndata: [DONE]\n\n')
          )
          controller.close()
        },
      })

      vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
        new Response(stream, { status: 200 })
      )

      const received: string[] = []
      await api.sendMessage('hi', (chunk) => received.push(chunk))
      expect(received).toEqual(['Hi'])
    })
  })

  describe('sendMessage SSE parsing', () => {
    let originalFetch: typeof fetch

    afterEach(() => {
      if (originalFetch) globalThis.fetch = originalFetch
    })

    function mockSseResponse(body: string): Response {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(body))
          controller.close()
        },
      })
      return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    }

    it('forwards content chunks to onChunk', async () => {
      originalFetch = globalThis.fetch
      const body = 'data: {"content":"hello"}\n\ndata: {"content":" world"}\n\ndata: [DONE]\n\n'
      globalThis.fetch = vi.fn().mockResolvedValue(mockSseResponse(body))

      const chunks: string[] = []
      await api.sendMessage('hi', (c) => chunks.push(c))
      expect(chunks.join('')).toBe('hello world')
    })

    it('fires onMeta when an event: meta line precedes a JSON data line', async () => {
      originalFetch = globalThis.fetch
      const body =
        'data: {"content":"hi"}\n\n' +
        'event: meta\ndata: {"profile_mutated": true}\n\n' +
        'data: [DONE]\n\n'
      globalThis.fetch = vi.fn().mockResolvedValue(mockSseResponse(body))

      const onMeta = vi.fn()
      await api.sendMessage('hi', () => {}, undefined, onMeta)
      expect(onMeta).toHaveBeenCalledWith({ profile_mutated: true })
    })

    it('ignores unknown event types', async () => {
      originalFetch = globalThis.fetch
      const body =
        'data: {"content":"hi"}\n\n' +
        'event: ping\ndata: {"x":1}\n\n' +
        'data: [DONE]\n\n'
      globalThis.fetch = vi.fn().mockResolvedValue(mockSseResponse(body))

      const onMeta = vi.fn()
      await api.sendMessage('hi', () => {}, undefined, onMeta)
      expect(onMeta).not.toHaveBeenCalled()
    })
  })
})
