import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { CoverLetterEditor } from './CoverLetterEditor'
import type { Document } from '../../api/client'

function withQuery(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

const baseDoc: Document = {
  id: 'd1', doc_type: 'cover_letter', content_md: 'Dear team,\n\nMy pitch.',
  structured_content: null, has_edits: false, generation_model: 'gemini-2.5-pro',
  created_at: new Date().toISOString(),
}

describe('CoverLetterEditor', () => {
  it('renders the Generate button when no document is present', () => {
    render(withQuery(<CoverLetterEditor appId="app-1" doc={null} status="none" />))
    expect(screen.getByRole('button', { name: /generate cover letter/i })).toBeInTheDocument()
  })

  it('renders the editor textarea when a document is present', () => {
    render(withQuery(<CoverLetterEditor appId="app-1" doc={baseDoc} status="ready" />))
    expect(screen.getByLabelText(/cover letter/i)).toHaveValue(baseDoc.content_md)
  })

  it('clicking Generate calls POST /api/applications/:id/cover-letter', async () => {
    let called = false
    server.use(
      http.post('/api/applications/app-1/cover-letter', () => {
        called = true
        return HttpResponse.json({
          id: 'd1', doc_type: 'cover_letter', content_md: 'gen', generation_model: 'gemini-2.5-pro',
          created_at: new Date().toISOString(),
        })
      }),
    )
    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={null} status="none" />))
    await user.click(screen.getByRole('button', { name: /generate cover letter/i }))
    await waitFor(() => expect(called).toBe(true))
  })

  it('shows an error toast on generation failure', async () => {
    server.use(
      http.post('/api/applications/app-1/cover-letter', () =>
        HttpResponse.json({ detail: 'rate limited' }, { status: 429 })),
    )
    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={null} status="none" />))
    await user.click(screen.getByRole('button', { name: /generate cover letter/i }))
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/rate limited/i))
  })

  it('PDF download fetches with the Authorization header (not a plain anchor)', async () => {
    // Plain <a href={pdfUrl}> would 401 in production because browser
    // navigation drops custom headers. The fix routes the click through
    // fetch so the auth header is applied, then triggers a blob download
    // client-side. This test asserts the contract that matters — the
    // auth-header-bearing fetch — and stops there. The post-fetch
    // mechanics (URL.createObjectURL → synthetic <a> → click → revoke)
    // are browser-mediated and not reliably observable in jsdom.
    sessionStorage.setItem('access_token', 'fake-jwt')
    const originalFetch = globalThis.fetch
    let receivedAuthHeader: string | null = null
    let receivedUrl: string | null = null
    globalThis.fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (typeof url === 'string' && url.includes('/api/documents/d1/pdf')) {
        receivedUrl = url
        const h = new Headers(init?.headers as HeadersInit)
        receivedAuthHeader = h.get('Authorization')
        return Promise.resolve(new Response(new Blob(['%PDF-1.4'], { type: 'application/pdf' }), {
          status: 200, headers: { 'Content-Type': 'application/pdf' },
        }))
      }
      return originalFetch(url, init)
    }) as typeof fetch

    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={baseDoc} status="ready" />))
    await user.click(screen.getByRole('button', { name: /pdf/i }))

    await waitFor(() => expect(receivedUrl).toBe('/api/documents/d1/pdf'))
    expect(receivedAuthHeader).toBe('Bearer fake-jwt')

    globalThis.fetch = originalFetch
    sessionStorage.removeItem('access_token')
  })

  it('PDF download surfaces an error toast if the fetch returns 401', async () => {
    sessionStorage.setItem('access_token', 'fake-jwt')
    const originalFetch = globalThis.fetch
    globalThis.fetch = vi.fn().mockImplementation((url: string) => {
      if (typeof url === 'string' && url.includes('/api/documents/d1/pdf')) {
        return Promise.resolve(new Response(JSON.stringify({ detail: 'Not authenticated' }), {
          status: 401, headers: { 'Content-Type': 'application/json' },
        }))
      }
      return originalFetch(url)
    }) as typeof fetch

    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={baseDoc} status="ready" />))
    await user.click(screen.getByRole('button', { name: /pdf/i }))

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/not authenticated/i))

    globalThis.fetch = originalFetch
    sessionStorage.removeItem('access_token')
  })

  afterEach(() => {
    sessionStorage.removeItem('access_token')
  })

  it('Save edits PATCHes the document', async () => {
    let body: unknown = null
    server.use(
      http.patch('/api/applications/app-1/documents/d1', async ({ request }) => {
        body = await request.json()
        return HttpResponse.json({ id: 'd1', saved: true })
      }),
    )
    const user = userEvent.setup()
    render(withQuery(<CoverLetterEditor appId="app-1" doc={baseDoc} status="ready" />))
    await user.clear(screen.getByLabelText(/cover letter/i))
    await user.type(screen.getByLabelText(/cover letter/i), 'edited')
    await user.click(screen.getByRole('button', { name: /save edits/i }))
    await waitFor(() => expect(body).toMatchObject({ user_edited_md: 'edited' }))
  })
})
