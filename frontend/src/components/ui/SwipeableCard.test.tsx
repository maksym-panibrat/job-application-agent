import { describe, it, expect, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SwipeableCard } from './SwipeableCard'

describe('SwipeableCard', () => {
  it('renders children and the revealed action', () => {
    render(
      <SwipeableCard onCommit={() => {}} actionLabel="Dismiss">
        <div>card content</div>
      </SwipeableCard>
    )
    expect(screen.getByText('card content')).toBeInTheDocument()
    expect(screen.getByText('Dismiss')).toBeInTheDocument()
  })

  it('calls onCommit when dragged past threshold and released', () => {
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 60, pointerId: 1 })
    fireEvent.pointerUp(surface, { clientX: 60, pointerId: 1 })
    expect(onCommit).toHaveBeenCalled()
  })

  it('does NOT commit when released before threshold', () => {
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 90, pointerId: 1 })
    fireEvent.pointerUp(surface, { clientX: 90, pointerId: 1 })
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('does not capture rightward drag (we only dismiss leftward)', () => {
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 200, pointerId: 1 })
    fireEvent.pointerUp(surface, { clientX: 200, pointerId: 1 })
    expect(onCommit).not.toHaveBeenCalled()
  })
})
