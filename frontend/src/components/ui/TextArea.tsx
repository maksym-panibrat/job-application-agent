import { forwardRef, TextareaHTMLAttributes, useEffect, useId, useRef } from 'react'
import { cn } from '../../lib/cn'

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string
  error?: string
}

export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { label, error, id, className, value, rows, onChange, ...rest },
  forwardedRef,
) {
  const auto = useId()
  const inputId = id ?? auto
  const localRef = useRef<HTMLTextAreaElement | null>(null)

  // Auto-resize: grow to fit content. Caller can still pass rows= for an
  // initial floor; we only grow beyond that.
  useEffect(() => {
    const el = localRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={inputId} className="text-xs text-muted">{label}</label>
      <textarea
        ref={(node) => {
          localRef.current = node
          if (typeof forwardedRef === 'function') forwardedRef(node)
          else if (forwardedRef) forwardedRef.current = node
        }}
        id={inputId}
        rows={rows ?? 3}
        aria-invalid={error ? true : undefined}
        value={value}
        onChange={onChange}
        className={cn(
          'bg-surface border border-border rounded-md-token px-3 py-2.5 text-sm text-text font-mono',
          'focus:outline-2 focus:outline-accent/40 focus:outline-offset-2 focus:border-accent',
          'resize-none overflow-hidden leading-relaxed',
          error && 'border-danger',
          className,
        )}
        {...rest}
      />
      {error && <span className="text-xs text-danger">{error}</span>}
    </div>
  )
})
