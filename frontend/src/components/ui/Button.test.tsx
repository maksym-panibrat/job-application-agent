import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button } from './Button'

describe('Button', () => {
  it('renders children', () => {
    render(<Button>Sync now</Button>)
    expect(screen.getByRole('button', { name: 'Sync now' })).toBeInTheDocument()
  })

  it('defaults to type="button" (not submit)', () => {
    render(<Button>Click</Button>)
    expect(screen.getByRole('button')).toHaveAttribute('type', 'button')
  })

  it('calls onClick when clicked', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<Button onClick={fn}>Hit me</Button>)
    await user.click(screen.getByRole('button'))
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('disables interaction and shows pending label when pending', async () => {
    const fn = vi.fn()
    const user = userEvent.setup()
    render(<Button pending onClick={fn}>Sync</Button>)
    const btn = screen.getByRole('button')
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('aria-busy', 'true')
    await user.click(btn)
    expect(fn).not.toHaveBeenCalled()
  })

  it('applies variant-specific class for primary (default)', () => {
    render(<Button>Default</Button>)
    expect(screen.getByRole('button').className).toMatch(/bg-accent/)
  })

  it('applies variant-specific class for secondary', () => {
    render(<Button variant="secondary">Sec</Button>)
    expect(screen.getByRole('button').className).toMatch(/border-border-strong/)
  })

  it('applies variant-specific class for ghost', () => {
    render(<Button variant="ghost">G</Button>)
    expect(screen.getByRole('button').className).toMatch(/text-muted/)
  })

  it('applies variant-specific class for destructive', () => {
    render(<Button variant="destructive">D</Button>)
    expect(screen.getByRole('button').className).toMatch(/bg-danger/)
  })

  it('size sm has shorter min-height than md', () => {
    render(
      <>
        <Button size="sm" data-testid="b-sm">sm</Button>
        <Button size="md" data-testid="b-md">md</Button>
      </>
    )
    expect(screen.getByTestId('b-sm').className).toMatch(/min-h-\[32px\]/)
    expect(screen.getByTestId('b-md').className).toMatch(/min-h-\[40px\]/)
  })

  it('size lg has larger min-height than md', () => {
    render(<Button size="lg" data-testid="b-lg">lg</Button>)
    expect(screen.getByTestId('b-lg').className).toMatch(/min-h-\[48px\]/)
  })

  it('forwards extra className', () => {
    render(<Button className="extra-thing">x</Button>)
    expect(screen.getByRole('button').className).toMatch(/extra-thing/)
  })
})
