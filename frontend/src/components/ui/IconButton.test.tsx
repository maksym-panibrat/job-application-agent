import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { IconButton } from './IconButton'

describe('IconButton', () => {
  it('renders the icon child and exposes the aria-label', () => {
    render(<IconButton aria-label="Close"><span>X</span></IconButton>)
    expect(screen.getByRole('button', { name: 'Close' })).toBeInTheDocument()
  })

  it('forwards onClick', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<IconButton aria-label="More" onClick={fn}><span>⋯</span></IconButton>)
    await user.click(screen.getByRole('button'))
    expect(fn).toHaveBeenCalled()
  })

  it('renders with at-least-44px tap target', () => {
    render(<IconButton aria-label="X"><span>x</span></IconButton>)
    expect(screen.getByRole('button').className).toMatch(/w-11/)
    expect(screen.getByRole('button').className).toMatch(/h-11/)
  })

  it('forwards extra className', () => {
    render(<IconButton aria-label="X" className="custom"><span>x</span></IconButton>)
    expect(screen.getByRole('button').className).toMatch(/custom/)
  })
})
