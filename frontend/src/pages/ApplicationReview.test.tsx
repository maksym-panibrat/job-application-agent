import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { server } from '../test/server'
import { ToastProvider } from '../components/ui/Toast'
import ApplicationReview from './ApplicationReview'
import type { ApplicationDetail } from '../api/client'

function detail(over: Partial<ApplicationDetail> = {}): ApplicationDetail {
  return {
    id: 'a1', status: 'pending_review', generation_status: 'none',
    match_score: 0.87, match_summary: 'Strong fit',
    match_rationale: null, match_strengths: ['Go'], match_gaps: ['No ML'],
    created_at: new Date().toISOString(),
    applied_at: null,
    job: {
      id: 'j', title: 'Senior Backend Engineer', company_name: 'Acme',
      location: 'Berlin', workplace_type: 'hybrid', salary: '€100k',
      contract_type: null, description_md: 'Acme is hiring.', description_clean: null,
      apply_url: 'https://x.com/', posted_at: null,
    },
    documents: [],
    generation_attempts: 0,
    ...over,
  }
}

function renderAt(initialEntry: string, mock: ApplicationDetail) {
  server.use(http.get('/api/applications/a1', () => HttpResponse.json(mock)))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/matches/:id" element={<ApplicationReview />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('Match detail (ApplicationReview)', () => {
  it('renders hero, match analysis, description, and editor placeholder', async () => {
    renderAt('/matches/a1', detail())
    await waitFor(() => expect(screen.getByRole('heading', { name: /senior backend engineer/i })).toBeInTheDocument())
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText('87% match')).toBeInTheDocument()
    expect(screen.getByText(/acme is hiring/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /generate cover letter/i })).toBeInTheDocument()
  })

  it('renders the cover letter editor when a document exists', async () => {
    renderAt('/matches/a1', detail({
      generation_status: 'ready',
      documents: [{
        id: 'd1', doc_type: 'cover_letter', content_md: 'Dear team,', structured_content: null,
        has_edits: false, generation_model: 'gemini-2.5-pro', created_at: new Date().toISOString(),
      }],
    }))
    await waitFor(() => expect(screen.getByLabelText(/cover letter/i)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: /save edits/i })).toBeInTheDocument()
  })

  it('shows the loading state before the fetch completes', () => {
    server.use(http.get('/api/applications/a1', async () => {
      await new Promise((r) => setTimeout(r, 50))
      return HttpResponse.json(detail())
    }))
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <ToastProvider>
          <MemoryRouter initialEntries={['/matches/a1']}>
            <Routes>
              <Route path="/matches/:id" element={<ApplicationReview />} />
            </Routes>
          </MemoryRouter>
        </ToastProvider>
      </QueryClientProvider>
    )
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })
})
