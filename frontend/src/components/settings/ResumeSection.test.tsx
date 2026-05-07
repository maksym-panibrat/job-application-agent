import { describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/server'
import { ToastProvider } from '../ui/Toast'
import { ResumeSection } from './ResumeSection'

function withCtx(node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>{node}</ToastProvider>
    </QueryClientProvider>
  )
}

describe('ResumeSection', () => {
  it('renders the empty state when no resume is uploaded', () => {
    render(withCtx(<ResumeSection hasResume={false} />))
    expect(screen.getByText(/no resume on file/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /upload resume/i })).toBeInTheDocument()
  })

  it('renders the present state with re-upload action', () => {
    render(withCtx(<ResumeSection hasResume />))
    expect(screen.getByText(/resume on file/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /re-upload/i })).toBeInTheDocument()
  })

  it('shows a success toast after upload succeeds', async () => {
    server.use(
      http.post('/api/profile/upload', () => HttpResponse.json({
        id: 'p-1', base_resume_md: 'parsed', extraction_status: 'ok', message: 'ok',
      })),
    )
    const user = userEvent.setup()
    render(withCtx(<ResumeSection hasResume={false} />))
    const input = screen.getByTestId('resume-file-input') as HTMLInputElement
    const file = new File(['pdf bytes'], 'resume.pdf', { type: 'application/pdf' })
    await user.upload(input, file)
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/uploaded/i))
  })
})
