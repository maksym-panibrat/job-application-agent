import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ToastProvider } from '../ui/Toast'
import { CoachDrawer } from './CoachDrawer'

function withCtx(initialEntry: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <MemoryRouter initialEntries={[initialEntry]}>
          <CoachDrawer />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}

describe('CoachDrawer', () => {
  it('renders nothing when ?coach is absent', () => {
    render(withCtx('/'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders the drawer when ?coach=1 is present', () => {
    render(withCtx('/?coach=1'))
    const dlg = screen.getByRole('dialog')
    expect(dlg).toBeInTheDocument()
    expect(dlg).toHaveAttribute('aria-label', 'Coach')
  })

  it('closing the drawer removes ?coach from the URL', async () => {
    const user = userEvent.setup()
    render(withCtx('/?coach=1&status=applied'))
    await user.click(screen.getByRole('button', { name: /close drawer/i }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('passes a known prompt slug as initialPrompt to Coach', () => {
    render(withCtx('/?coach=1&prompt=set_locations'))
    const input = screen.getByPlaceholderText(/type your/i) as HTMLInputElement
    expect(input.value.toLowerCase()).toContain('location')
  })

  it('unknown prompt slug falls back to empty composer', () => {
    render(withCtx('/?coach=1&prompt=this_is_not_a_real_slug'))
    const input = screen.getByPlaceholderText(/type your/i) as HTMLInputElement
    expect(input.value).toBe('')
  })
})
