import { ReactNode, useState } from 'react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../test/server'
import { ToastProvider } from './ui/Toast'
import { FeedbackModal } from './FeedbackModal'

function withCtx(node: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

function StatefulFeedbackModal() {
  const [open, setOpen] = useState(true)
  return <FeedbackModal open={open} onClose={() => setOpen(false)} />
}

describe('FeedbackModal', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/')
    document.title = 'Job Search'
  })

  it('renders category dropdown with Feature request selected by default', () => {
    render(withCtx(<FeedbackModal open onClose={() => {}} />))

    const select = screen.getByRole('combobox', { name: 'Category' })
    expect(select).toHaveValue('feature_request')
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Feature request',
      'Bug',
      'Other',
    ])
  })

  it('does not allow an empty message to submit', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    let posted = false
    server.use(
      http.post('/api/feedback', () => {
        posted = true
        return HttpResponse.json({
          id: 'feedback-1',
          created: true,
          notification_status: 'sent',
        })
      }),
    )

    render(withCtx(<FeedbackModal open onClose={onClose} />))
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    await user.type(screen.getByLabelText('What happened?'), '   ')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(posted).toBe(false)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('submits feedback with diagnostics, closes, and treats notification failure as success', async () => {
    window.history.pushState({}, '', '/matches/app-123?tab=cover-letter')
    document.title = 'Application Review'
    const user = userEvent.setup()
    let posted: unknown = null
    server.use(
      http.post('/api/feedback', async ({ request }) => {
        posted = await request.json()
        return HttpResponse.json({
          id: 'feedback-1',
          created: true,
          notification_status: 'failed',
        })
      }),
    )

    render(withCtx(<StatefulFeedbackModal />))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Category' }), 'other')
    await user.type(screen.getByLabelText('What happened?'), 'The cover letter looks stale.')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.getByRole('status')).toHaveTextContent('Feedback sent')
    expect(posted).toMatchObject({
      category: 'other',
      message: 'The cover letter looks stale.',
      diagnostics: {
        path: '/matches/app-123?tab=cover-letter',
        page_title: 'Application Review',
        user_agent: window.navigator.userAgent,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        route_context: { application_id: 'app-123' },
      },
    })
    expect(new Date((posted as { diagnostics: { reported_at_client: string } }).diagnostics.reported_at_client).toString()).not.toBe('Invalid Date')
  })

  it('keeps the message and modal open when the API fails', async () => {
    const user = userEvent.setup()
    server.use(
      http.post('/api/feedback', () =>
        HttpResponse.json({ detail: 'nope' }, { status: 500 }),
      ),
    )

    render(withCtx(<StatefulFeedbackModal />))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Category' }), 'bug')
    await user.type(screen.getByLabelText('What happened?'), 'This button does nothing.')
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Could not send feedback. Try again.')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByLabelText('What happened?')).toHaveValue('This button does nothing.')
    expect(screen.getByRole('combobox', { name: 'Category' })).toHaveValue('bug')
  })
})
