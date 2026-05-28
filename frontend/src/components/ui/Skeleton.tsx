import { HTMLAttributes } from 'react'
import { cn } from '../../lib/cn'

interface SkeletonLineProps extends HTMLAttributes<HTMLDivElement> {
  width?: string | number
  height?: string | number
}

function SkeletonLine({ width, height, style, className, ...rest }: SkeletonLineProps) {
  const merged = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height ?? '12px',
    ...style,
  }
  return (
    <div
      data-skel-line
      className={cn('bg-surface-2 rounded-sm-token animate-pulse', className)}
      style={merged}
      {...rest}
    />
  )
}

export function SkeletonCard() {
  return (
    <div className="bg-surface border border-border rounded-lg-token p-4 space-y-2">
      <SkeletonLine width="30%" height={14} />
      <SkeletonLine width="70%" height={18} />
      <SkeletonLine width="40%" height={14} />
    </div>
  )
}
