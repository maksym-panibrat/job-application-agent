import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SkeletonLine, SkeletonCard } from './Skeleton'

describe('Skeleton', () => {
  it('SkeletonLine renders with shimmer class', () => {
    render(<SkeletonLine data-testid="s" />)
    expect(screen.getByTestId('s').className).toMatch(/animate-pulse/)
  })

  it('SkeletonLine respects width and height props', () => {
    render(<SkeletonLine data-testid="s" width="50%" height={20} />)
    const el = screen.getByTestId('s')
    expect(el.style.width).toBe('50%')
    expect(el.style.height).toBe('20px')
  })

  it('SkeletonCard renders multiple lines', () => {
    const { container } = render(<SkeletonCard />)
    const lines = container.querySelectorAll('[data-skel-line]')
    expect(lines.length).toBeGreaterThanOrEqual(3)
  })
})
