import { HTMLAttributes, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export type BadgeIntent = 'success' | 'warning' | 'danger' | 'muted'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  intent?: BadgeIntent
  children: ReactNode
}

const intentClass: Record<BadgeIntent, string> = {
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/10 text-warning',
  danger:  'bg-danger/10 text-danger',
  muted:   'bg-surface-2 text-muted',
}

export function Badge({ intent = 'success', className, children, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 rounded-sm-token text-xs font-semibold',
        intentClass[intent],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  )
}
