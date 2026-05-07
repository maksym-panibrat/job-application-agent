import { AnchorHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { Link, LinkProps } from 'react-router-dom'
import { cn } from '../../lib/cn'

type Common = {
  interactive?: boolean
  className?: string
  children: ReactNode
}

type DivCardProps = Common & HTMLAttributes<HTMLDivElement> & { as?: 'div' }
type AnchorCardProps = Common & AnchorHTMLAttributes<HTMLAnchorElement> & { as: 'a' }
type RRLinkCardProps = Common & LinkProps & { as: 'rrlink' }

export type CardProps = DivCardProps | AnchorCardProps | RRLinkCardProps

const baseClass =
  'block bg-surface border border-border rounded-lg-token p-4 transition-colors duration-[var(--t-fast)]'

const interactiveClass = 'cursor-pointer hover:border-border-strong'

export function Card(props: CardProps) {
  if (props.as === 'a') {
    const { as: _as, interactive, className, children, ...rest } = props
    return (
      <a
        className={cn(baseClass, interactive && interactiveClass, className)}
        {...rest}
      >
        {children}
      </a>
    )
  }
  if (props.as === 'rrlink') {
    const { as: _as, interactive, className, children, ...rest } = props
    return (
      <Link
        className={cn(baseClass, interactive && interactiveClass, className)}
        {...rest}
      >
        {children}
      </Link>
    )
  }
  // default: div
  const { as: _as, interactive, className, children, ...rest } = props as DivCardProps
  return (
    <div
      className={cn(baseClass, interactive && interactiveClass, className)}
      {...rest}
    >
      {children}
    </div>
  )
}
