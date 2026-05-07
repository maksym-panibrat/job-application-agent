import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Chip } from './Chip'

describe('Chip', () => {
  it('renders label and count', () => {
    render(<Chip count={12}>Pending</Chip>)
    expect(screen.getByText('Pending')).toBeInTheDocument()
    expect(screen.getByText('12')).toBeInTheDocument()
  })

  it('renders without count when omitted', () => {
    render(<Chip>Applied</Chip>)
    expect(screen.getByText('Applied')).toBeInTheDocument()
    expect(screen.queryByText(/^\d+$/)).not.toBeInTheDocument()
  })

  it('signals selected state via aria-pressed and accent class', () => {
    render(<Chip selected>Sel</Chip>)
    const btn = screen.getByRole('button', { name: 'Sel' })
    expect(btn).toHaveAttribute('aria-pressed', 'true')
    expect(btn.className).toMatch(/bg-accent/)
  })

  it('non-selected reports aria-pressed=false', () => {
    render(<Chip>Off</Chip>)
    expect(screen.getByRole('button', { name: 'Off' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('calls onClick', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<Chip onClick={fn}>Hit</Chip>)
    await user.click(screen.getByRole('button'))
    expect(fn).toHaveBeenCalled()
  })
})
