import { ReactNode, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { IconButton } from './IconButton'
import { Close } from './icons'
import { cn } from '../../lib/cn'

export interface DrawerProps {
  open: boolean
  onClose: () => void
  /** Accessible name for the dialog and visible header. */
  title: string
  children: ReactNode
  /** Optional class added to the inner panel — e.g. for layout overrides. */
  className?: string
}

export function Drawer({ open, onClose, title, children, className }: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    panelRef.current?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={title}>
      <div
        data-testid="drawer-backdrop"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className={cn(
          'absolute top-0 bottom-0 right-0 bg-surface border-l border-border',
          'w-full md:w-[420px]',
          'flex flex-col outline-none',
          className,
        )}
      >
        <header className="flex items-center justify-between p-3 border-b border-border">
          <h2 className="text-base font-semibold text-text">{title}</h2>
          <IconButton aria-label="Close drawer" onClick={onClose}>
            <Close className="w-4 h-4" />
          </IconButton>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
