import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { useStatusFilter } from './useStatusFilter'

function Probe() {
  const { status, setStatus } = useStatusFilter()
  return (
    <div>
      <span data-testid="status">{status}</span>
      <button onClick={() => setStatus('applied')}>to-applied</button>
      <button onClick={() => setStatus('dismissed')}>to-dismissed</button>
      <button onClick={() => setStatus('pending')}>to-pending</button>
    </div>
  )
}

function renderWith(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Probe />
    </MemoryRouter>
  )
}

describe('useStatusFilter', () => {
  it('defaults to "pending" when no ?status= is present', () => {
    renderWith('/')
    expect(screen.getByTestId('status').textContent).toBe('pending')
  })

  it('reads ?status=applied from the URL', () => {
    renderWith('/?status=applied')
    expect(screen.getByTestId('status').textContent).toBe('applied')
  })

  it('reads ?status=dismissed', () => {
    renderWith('/?status=dismissed')
    expect(screen.getByTestId('status').textContent).toBe('dismissed')
  })

  it('coerces unknown values back to "pending"', () => {
    renderWith('/?status=bogus')
    expect(screen.getByTestId('status').textContent).toBe('pending')
  })

  it('setStatus updates the URL param', async () => {
    const user = userEvent.setup()
    renderWith('/')
    await user.click(screen.getByText('to-applied'))
    expect(screen.getByTestId('status').textContent).toBe('applied')
  })

  it('setStatus(pending) removes the param entirely (cleaner URLs)', async () => {
    const user = userEvent.setup()
    renderWith('/?status=applied')
    await user.click(screen.getByText('to-pending'))
    expect(screen.getByTestId('status').textContent).toBe('pending')
  })
})
