import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { SkeletonCard } from './Skeleton'

describe('Skeleton', () => {
  it('SkeletonCard renders multiple lines', () => {
    const { container } = render(<SkeletonCard />)
    const lines = container.querySelectorAll('[data-skel-line]')
    expect(lines.length).toBeGreaterThanOrEqual(3)
  })
})
