import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ScoreBadge } from './ScoreBadge'

describe('ScoreBadge', () => {
  it('returns null when score is null', () => {
    const { container } = render(<ScoreBadge score={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders the rounded percentage', () => {
    render(<ScoreBadge score={0.873} />)
    expect(screen.getByText('87% match')).toBeInTheDocument()
  })

  it('uses success intent for ≥80% scores', () => {
    const { container } = render(<ScoreBadge score={0.82} />)
    expect(container.firstChild).toHaveClass('text-success')
  })

  it('uses warning intent for 65–79%', () => {
    const { container } = render(<ScoreBadge score={0.7} />)
    expect(container.firstChild).toHaveClass('text-warning')
  })

  it('uses muted intent for <65%', () => {
    const { container } = render(<ScoreBadge score={0.5} />)
    expect(container.firstChild).toHaveClass('text-muted')
  })
})
