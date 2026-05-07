import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Card } from './Card'

describe('Card', () => {
  it('renders as a div by default', () => {
    render(<Card data-testid="c">child</Card>)
    expect(screen.getByTestId('c').tagName).toBe('DIV')
  })

  it('renders as an anchor when as="a" is provided', () => {
    render(<Card data-testid="c" as="a" href="/path">link</Card>)
    const el = screen.getByTestId('c')
    expect(el.tagName).toBe('A')
    expect(el).toHaveAttribute('href', '/path')
  })

  it('renders as a react-router Link when as="rrlink" is provided', () => {
    render(
      <MemoryRouter>
        <Card data-testid="c" as="rrlink" to="/route">link</Card>
      </MemoryRouter>
    )
    const el = screen.getByTestId('c')
    expect(el.tagName).toBe('A')
    expect(el).toHaveAttribute('href', '/route')
  })

  it('applies surface + border by default', () => {
    render(<Card data-testid="c">x</Card>)
    expect(screen.getByTestId('c').className).toMatch(/bg-surface/)
    expect(screen.getByTestId('c').className).toMatch(/border-border/)
  })

  it('adds interactive hover class when interactive', () => {
    render(<Card data-testid="c" interactive>x</Card>)
    expect(screen.getByTestId('c').className).toMatch(/hover:border-border-strong/)
    expect(screen.getByTestId('c').className).toMatch(/cursor-pointer/)
  })
})
