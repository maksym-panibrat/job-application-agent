import { ButtonHTMLAttributes, forwardRef, ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface ChipProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  selected?: boolean
  count?: number
  children: ReactNode
}

export const Chip = forwardRef<HTMLButtonElement, ChipProps>(function Chip(
  { selected = false, count, className, children, type, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type ?? 'button'}
      aria-pressed={selected}
      className={cn(
        'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-pill border text-sm transition-colors duration-[var(--t-fast)] min-h-[32px]',
        selected
          ? 'bg-accent/15 text-accent border-accent/40'
          : 'bg-surface text-muted border-border hover:text-text',
        className,
      )}
      {...rest}
    >
      <span>{children}</span>
      {count !== undefined && (
        <span
          className={cn(
            'px-1.5 rounded-pill text-xs',
            selected ? 'bg-accent/30 text-accent' : 'bg-bg text-subtle',
          )}
        >
          {count}
        </span>
      )}
    </button>
  )
})
