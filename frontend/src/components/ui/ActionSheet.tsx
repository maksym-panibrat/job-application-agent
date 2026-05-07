import { ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/cn'

export interface ActionSheetProps {
  open: boolean
  onClose: () => void
  /** Accessible label for the dialog. Not necessarily rendered. */
  title: string
  /** Optional visible heading rendered at the top of the sheet. */
  heading?: ReactNode
  children: ReactNode
}

export function ActionSheet({ open, onClose, title, heading, children }: ActionSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    sheetRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center md:justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        data-testid="actionsheet-backdrop"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <div
        ref={sheetRef}
        tabIndex={-1}
        className={cn(
          'relative bg-surface-2 border border-border w-full max-w-md',
          'rounded-t-lg-token md:rounded-lg-token',
          'md:max-w-sm',
          'p-2 outline-none',
        )}
      >
        <div className="md:hidden flex justify-center pt-2 pb-3">
          <span className="block w-10 h-1 rounded-pill bg-border-strong" />
        </div>
        {heading && (
          <div className="px-3 pb-2 text-sm font-semibold text-text">{heading}</div>
        )}
        <div className="flex flex-col">{children}</div>
      </div>
    </div>,
    document.body,
  )
}

/** Convenience item used inside an ActionSheet. */
export interface ActionSheetItemProps {
  onClick?: () => void
  intent?: 'default' | 'danger'
  disabled?: boolean
  children: ReactNode
}

export function ActionSheetItem({ onClick, intent = 'default', disabled = false, children }: ActionSheetItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'text-left px-4 py-3 text-sm border-b border-border last:border-b-0 hover:bg-surface',
        'disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent',
        intent === 'danger' ? 'text-danger' : 'text-text',
      )}
    >
      {children}
    </button>
  )
}
