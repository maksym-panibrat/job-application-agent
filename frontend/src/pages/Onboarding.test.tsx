import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import Onboarding from './Onboarding'

vi.mock('../api/client', () => ({
  api: {
    getProfile: vi.fn().mockResolvedValue(null),
    sendMessage: vi.fn(),
    uploadResume: vi.fn(),
  },
}))

import { api } from '../api/client'

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('Onboarding', () => {
  it('shows error message and re-enables Send when chat request fails', async () => {
    vi.mocked(api.sendMessage).mockImplementation((_msg, _onChunk, onError) => {
      onError?.(new Error('500: Server Error'))
      return Promise.resolve()
    })

    render(<Onboarding />, { wrapper })

    const input = screen.getByPlaceholderText('Type your preferences...')
    const sendBtn = screen.getByRole('button', { name: 'Send' })

    fireEvent.change(input, { target: { value: 'hello' } })
    fireEvent.click(sendBtn)

    await waitFor(() => {
      expect(screen.getByText('Something went wrong — please try again.')).toBeInTheDocument()
    })

    // After error, sending should be reset so typing re-enables the button
    fireEvent.change(input, { target: { value: 'retry' } })
    expect(sendBtn).not.toBeDisabled()
  })

  it('shows extraction error banner when resume upload returns parse_error', async () => {
    vi.mocked(api.uploadResume).mockResolvedValue({
      id: '00000000-0000-0000-0000-000000000001',
      base_resume_md: null,
      extraction_status: 'parse_error',
      message: 'Resume uploaded successfully.',
    })
    vi.mocked(api.sendMessage).mockImplementation((_msg, _onChunk, _onError) => Promise.resolve())

    render(<Onboarding />, { wrapper })

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['pdf content'], 'resume.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    await waitFor(() => {
      expect(
        screen.getByText(/couldn't read the structure/)
      ).toBeInTheDocument()
    })
  })
})
