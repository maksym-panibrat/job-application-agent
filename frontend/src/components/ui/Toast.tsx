import { createContext, ReactNode, useCallback, useContext, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../../lib/cn'

export type ToastIntent = 'success' | 'error' | 'info'

interface ToastEntry { id: number; message: string; intent: ToastIntent }

interface ToastContextValue { show: (message: string, intent?: ToastIntent) => void }

const ToastContext = createContext<ToastContextValue | null>(null)

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast() must be used inside <ToastProvider>')
  return ctx
}

const AUTO_DISMISS_MS = 4_000

const intentClass: Record<ToastIntent, string> = {
  success: 'border-l-success',
  error:   'border-l-danger',
  info:    'border-l-accent',
}

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const show = useCallback((message: string, intent: ToastIntent = 'info') => {
    const id = nextId++
    setToasts((prev) => [...prev, { id, message, intent }])
    setTimeout(() => dismiss(id), AUTO_DISMISS_MS)
  }, [dismiss])

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div className="fixed z-50 flex flex-col gap-2 pointer-events-none
                        bottom-4 left-1/2 -translate-x-1/2
                        md:left-auto md:right-4 md:translate-x-0">
          {toasts.map((t) => (
            <div
              key={t.id}
              role="status"
              className={cn(
                'pointer-events-auto bg-surface-2 border border-border border-l-4 px-4 py-3',
                'rounded-md-token text-sm text-text max-w-xs shadow-lg',
                intentClass[t.intent],
              )}
            >
              {t.message}
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  )
}
