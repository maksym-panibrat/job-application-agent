import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { Badge } from './Badge'

describe('Badge', () => {
  it('renders children', () => {
    render(<Badge>87% match</Badge>)
    expect(screen.getByText('87% match')).toBeInTheDocument()
  })

  it('uses success colors by default', () => {
    const { container } = render(<Badge>x</Badge>)
    expect(container.firstChild).toHaveClass('text-success')
  })

  it('renders warning intent', () => {
    const { container } = render(<Badge intent="warning">x</Badge>)
    expect(container.firstChild).toHaveClass('text-warning')
  })

  it('renders danger intent', () => {
    const { container } = render(<Badge intent="danger">x</Badge>)
    expect(container.firstChild).toHaveClass('text-danger')
  })

  it('renders muted intent', () => {
    const { container } = render(<Badge intent="muted">x</Badge>)
    expect(container.firstChild).toHaveClass('text-muted')
  })
})
