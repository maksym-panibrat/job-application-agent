import { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface EmptyStateProps {
  title: string
  description: string
  icon?: ReactNode
  action?: ReactNode
  className?: string
}

export function EmptyState({ title, description, icon, action, className }: EmptyStateProps) {
  return (
    <div className={cn('text-center py-10 px-6 text-muted', className)}>
      {icon && <div className="text-2xl text-subtle mb-2">{icon}</div>}
      <p className="text-base font-semibold text-text mb-1">{title}</p>
      <p className="text-sm mb-4">{description}</p>
      {action}
    </div>
  )
}
