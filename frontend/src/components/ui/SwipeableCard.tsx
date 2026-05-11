import { ReactNode, useRef, useState } from 'react'
import { cn } from '../../lib/cn'

export interface SwipeableCardProps {
  children: ReactNode
  /** Called when the user swipes far enough left and releases. */
  onCommit: () => void
  /** Visible label of the action revealed underneath. */
  actionLabel: string
  /** Pixels of leftward travel required to commit. */
  thresholdPx?: number
  className?: string
}

// Pixels of total travel before we decide whether the gesture is a swipe or a
// vertical scroll. Small enough to feel responsive, large enough to ignore
// tap jitter.
const AXIS_LOCK_SLOP_PX = 10

type Axis = 'unlocked' | 'horizontal' | 'vertical'

export function SwipeableCard({
  children,
  onCommit,
  actionLabel,
  thresholdPx = 60,
  className,
}: SwipeableCardProps) {
  const [dx, setDx] = useState(0)
  const startX = useRef<number | null>(null)
  const startY = useRef<number | null>(null)
  const axis = useRef<Axis>('unlocked')

  function reset() {
    setDx(0)
    startX.current = null
    startY.current = null
    axis.current = 'unlocked'
  }

  function onPointerDown(e: React.PointerEvent) {
    startX.current = e.clientX
    startY.current = e.clientY
    axis.current = 'unlocked'
    // Note: we intentionally do NOT setPointerCapture here. Capturing on
    // pointerdown steals the gesture from the browser's native scroller, so
    // vertical scrolls that originate on the card become unresponsive. We
    // only need pointermove/up on this element, which the browser delivers
    // without explicit capture.
  }

  function onPointerMove(e: React.PointerEvent) {
    if (startX.current == null || startY.current == null) return
    const dxRaw = e.clientX - startX.current
    const dyRaw = e.clientY - startY.current

    if (axis.current === 'unlocked') {
      // Wait until the finger has moved past the slop radius, then lock the
      // axis based on which direction dominates.
      if (Math.abs(dxRaw) < AXIS_LOCK_SLOP_PX && Math.abs(dyRaw) < AXIS_LOCK_SLOP_PX) {
        return
      }
      axis.current = Math.abs(dxRaw) > Math.abs(dyRaw) ? 'horizontal' : 'vertical'
    }

    if (axis.current === 'vertical') return
    setDx(Math.min(0, dxRaw))
  }

  function onPointerUp(e: React.PointerEvent) {
    if (startX.current == null) {
      reset()
      return
    }
    const committed =
      axis.current === 'horizontal' &&
      Math.min(0, e.clientX - startX.current) <= -thresholdPx
    if (committed) onCommit()
    reset()
  }

  return (
    <div className={cn('relative select-none touch-pan-y', className)}>
      <div className="absolute inset-y-0 right-0 w-20 bg-danger flex items-center justify-center text-white text-xs font-bold rounded-r-lg-token">
        {actionLabel}
      </div>
      <div
        data-testid="swipe-surface"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        style={{ transform: `translateX(${dx}px)` }}
        className={cn(
          'relative bg-surface border border-border rounded-lg-token transition-transform duration-[var(--t-fast)] ease-token-ease',
          startX.current != null && 'transition-none',
        )}
      >
        {children}
      </div>
    </div>
  )
}
