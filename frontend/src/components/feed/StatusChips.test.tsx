import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { StatusChips } from './StatusChips'

function renderChips(initialEntry = '/') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <StatusChips counts={{ pending: 12, applied: 4, dismissed: 8 }} />
    </MemoryRouter>
  )
}

describe('StatusChips', () => {
  it('renders three chips with labels and counts', () => {
    renderChips()
    expect(screen.getByRole('button', { name: /pending/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /applied/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /dismissed/i })).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
  })

  it('marks the active chip via aria-pressed=true based on URL', () => {
    renderChips('/?status=applied')
    expect(screen.getByRole('button', { name: /applied/i })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: /pending/i })).toHaveAttribute('aria-pressed', 'false')
  })

  it('defaults pending to active when no status= present', () => {
    renderChips('/')
    expect(screen.getByRole('button', { name: /pending/i })).toHaveAttribute('aria-pressed', 'true')
  })

  it('clicking a chip updates the URL via the hook', async () => {
    const user = userEvent.setup()
    renderChips('/')
    await user.click(screen.getByRole('button', { name: /applied/i }))
    expect(screen.getByRole('button', { name: /applied/i })).toHaveAttribute('aria-pressed', 'true')
  })
})
