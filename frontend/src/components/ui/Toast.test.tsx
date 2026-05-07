import { describe, it, expect, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ToastProvider, useToast } from './Toast'

function Probe() {
  const toast = useToast()
  return (
    <div>
      <button onClick={() => toast.show('Saved', 'success')}>say-success</button>
      <button onClick={() => toast.show('Boom', 'error')}>say-error</button>
    </div>
  )
}

describe('Toast', () => {
  it('renders nothing when no toasts queued', () => {
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('shows a success toast on demand', async () => {
    const user = userEvent.setup()
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    await user.click(screen.getByText('say-success'))
    const t = screen.getByRole('status')
    expect(t).toHaveTextContent('Saved')
    expect(t.className).toMatch(/border-l-success/)
  })

  it('shows an error toast with the danger border', async () => {
    const user = userEvent.setup()
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>
    )
    await user.click(screen.getByText('say-error'))
    expect(screen.getByRole('status').className).toMatch(/border-l-danger/)
  })

  it('auto-dismisses after 5s', () => {
    vi.useFakeTimers()
    try {
      render(
        <ToastProvider>
          <Probe />
        </ToastProvider>
      )
      act(() => { fireEvent.click(screen.getByText('say-success')) })
      expect(screen.getByRole('status')).toBeInTheDocument()
      act(() => { vi.advanceTimersByTime(5_000) })
      expect(screen.queryByRole('status')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it('throws useful error if useToast() called outside provider', () => {
    function Bare() {
      useToast()
      return null
    }
    expect(() => render(<Bare />)).toThrow(/ToastProvider/)
  })
})
