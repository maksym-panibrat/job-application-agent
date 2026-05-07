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

export function SwipeableCard({
  children,
  onCommit,
  actionLabel,
  thresholdPx = 24,
  className,
}: SwipeableCardProps) {
  const [dx, setDx] = useState(0)
  const startX = useRef<number | null>(null)

  function onPointerDown(e: React.PointerEvent) {
    startX.current = e.clientX
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  function onPointerMove(e: React.PointerEvent) {
    if (startX.current == null) return
    const delta = e.clientX - startX.current
    setDx(Math.min(0, delta))
  }
  function onPointerUp(e: React.PointerEvent) {
    if (startX.current == null) return
    // Compute final delta directly from released pointer position to avoid
    // React state-batching issues in tests.
    const delta = Math.min(0, e.clientX - startX.current)
    if (delta <= -thresholdPx) onCommit()
    setDx(0)
    startX.current = null
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
