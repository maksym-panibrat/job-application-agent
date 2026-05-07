import { describe, it, expect } from 'vitest'
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
