import { FormEvent, useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { useMutation } from '@tanstack/react-query'
import { api, type FeedbackCategory, type FeedbackDiagnostics } from '../api/client'
import { cn } from '../lib/cn'
import { Button } from './ui/Button'
import { TextArea } from './ui/TextArea'
import { useToast } from './ui/Toast'

export interface FeedbackModalProps {
  open: boolean
  onClose: () => void
}

const categories: { value: FeedbackCategory; label: string }[] = [
  { value: 'feature_request', label: 'Feature request' },
  { value: 'bug', label: 'Bug' },
  { value: 'other', label: 'Other' },
]

function collectFeedbackDiagnostics(): FeedbackDiagnostics {
  const pathname = window.location.pathname
  const search = window.location.search
  const match = pathname.match(/^\/matches\/([^/?#]+)/)
  const routeContext: Record<string, string> = {}

  if (match?.[1]) {
    routeContext.application_id = decodeURIComponent(match[1])
  }

  return {
    reported_at_client: new Date().toISOString(),
    path: `${pathname}${search}`,
    page_title: document.title,
    user_agent: window.navigator.userAgent,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    route_context: routeContext,
  }
}

export function FeedbackModal({ open, onClose }: FeedbackModalProps) {
  const titleId = useId()
  const helperId = useId()
  const [category, setCategory] = useState<FeedbackCategory>('feature_request')
  const [message, setMessage] = useState('')
  const { show } = useToast()

  const submit = useMutation({
    mutationFn: () =>
      api.submitFeedback({
        category,
        message,
        diagnostics: collectFeedbackDiagnostics(),
      }),
    onSuccess: () => {
      setCategory('feature_request')
      setMessage('')
      onClose()
      show('Feedback sent', 'success')
    },
    onError: () => {
      show('Could not send feedback. Try again.', 'error')
    },
  })

  if (!open) return null

  const canSubmit = message.trim().length > 0 && !submit.isPending

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return
    submit.mutate()
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center md:justify-center"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={helperId}
    >
      <div
        data-testid="feedback-modal-backdrop"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
      />
      <form
        onSubmit={handleSubmit}
        className={cn(
          'relative w-full max-w-lg bg-surface-2 border border-border',
          'rounded-t-lg-token md:rounded-lg-token p-5 shadow-xl',
          'flex flex-col gap-5',
        )}
      >
        <div className="flex flex-col gap-1">
          <h2 id={titleId} className="text-base font-semibold text-text">Send feedback</h2>
          <p id={helperId} className="text-sm text-muted">Page details will be included automatically.</p>
        </div>

        <fieldset className="flex flex-col gap-2">
          <legend className="sr-only">Feedback category</legend>
          {categories.map((item) => (
            <label
              key={item.value}
              className={cn(
                'flex items-center gap-3 rounded-md-token border border-border bg-surface px-3 py-2 text-sm text-text',
                'has-[:checked]:border-accent has-[:checked]:bg-accent/10',
              )}
            >
              <input
                type="radio"
                name="feedback-category"
                value={item.value}
                checked={category === item.value}
                onChange={() => setCategory(item.value)}
                className="h-4 w-4 accent-accent"
              />
              <span>{item.label}</span>
            </label>
          ))}
        </fieldset>

        <TextArea
          label="What happened?"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          maxLength={5000}
          rows={4}
        />

        <div className="flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={!canSubmit} pending={submit.isPending}>
            Send
          </Button>
        </div>
      </form>
    </div>,
    document.body,
  )
}
