import { forwardRef, InputHTMLAttributes, useId } from 'react'
import { cn } from '../../lib/cn'

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(function TextField(
  { label, error, id, className, ...rest },
  ref,
) {
  const auto = useId()
  const inputId = id ?? auto

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-xs text-muted">{label}</label>
      <input
        ref={ref}
        id={inputId}
        aria-invalid={error ? true : undefined}
        className={cn(
          'bg-surface border border-border rounded-md-token px-3 py-2.5 text-sm text-text',
          'min-h-[44px] focus:outline-2 focus:outline-accent/40 focus:outline-offset-2 focus:border-accent',
          error && 'border-danger',
          className,
        )}
        {...rest}
      />
      {error && <span className="text-xs text-danger">{error}</span>}
    </div>
  )
})
