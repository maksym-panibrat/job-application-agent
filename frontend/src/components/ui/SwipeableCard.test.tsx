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

  it('does NOT commit on a vertical-dominant gesture (scroll), even past horizontal threshold', () => {
    // Reproduces the bug: user scrolls vertically while their finger drifts
    // leftward by more than thresholdPx. Y travel dominates X travel, so this
    // is unambiguously a scroll, not a swipe — must not dismiss.
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss" thresholdPx={24}>
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 100, clientY: 400, pointerId: 1 })
    // Simulate the moving finger over the course of the gesture so the axis
    // lock can see early movement is vertical.
    fireEvent.pointerMove(surface, { clientX: 95, clientY: 350, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 85, clientY: 280, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 70, clientY: 200, pointerId: 1 })
    fireEvent.pointerUp(surface, { clientX: 70, clientY: 200, pointerId: 1 })
    expect(onCommit).not.toHaveBeenCalled()
  })

  it('commits on a horizontal-dominant gesture past the (higher) default threshold', () => {
    // Default threshold is now larger; verify a clear horizontal swipe commits.
    const onCommit = vi.fn()
    render(
      <SwipeableCard onCommit={onCommit} actionLabel="Dismiss">
        <div>x</div>
      </SwipeableCard>
    )
    const surface = screen.getByTestId('swipe-surface')
    fireEvent.pointerDown(surface, { clientX: 200, clientY: 400, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 180, clientY: 402, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 140, clientY: 404, pointerId: 1 })
    fireEvent.pointerMove(surface, { clientX: 90, clientY: 406, pointerId: 1 })
    fireEvent.pointerUp(surface, { clientX: 90, clientY: 406, pointerId: 1 })
    expect(onCommit).toHaveBeenCalled()
  })
})
