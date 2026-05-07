import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('renders title and description', () => {
    render(<EmptyState title="Caught up" description="No new matches" />)
    expect(screen.getByText('Caught up')).toBeInTheDocument()
    expect(screen.getByText('No new matches')).toBeInTheDocument()
  })

  it('renders an icon when provided', () => {
    render(<EmptyState title="x" description="y" icon={<span data-testid="ic">○</span>} />)
    expect(screen.getByTestId('ic')).toBeInTheDocument()
  })

  it('renders an action node when provided', () => {
    render(
      <EmptyState
        title="x"
        description="y"
        action={<button>Sync now</button>}
      />
    )
    expect(screen.getByText('Sync now')).toBeInTheDocument()
  })

  it('renders without action / icon if absent', () => {
    render(<EmptyState title="x" description="y" />)
    expect(screen.getByText('x')).toBeInTheDocument()
  })
})
