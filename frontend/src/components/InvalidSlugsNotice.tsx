import { useEffect, useState } from 'react'
import { api } from '../api/client'

/**
 * Amber banner listing slugs the backend has auto-pruned from the user's
 * profile after repeated 404s from Greenhouse. Polls /api/sync/status every
 * 30s (slower than the 3s chip — invalid slugs change rarely).
 *
 * Dismissal is client-side only — refreshing the page brings back any
 * still-invalid slugs. Persistent dismissal would need a backend flag,
 * which is out of scope.
 */
export function InvalidSlugsNotice() {
  const [invalid, setInvalid] = useState<string[]>([])
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false

    async function load() {
      try {
        const body = await api.getSyncStatus()
        if (!cancelled) setInvalid(body.invalid_slugs ?? [])
      } catch {
        // Network error — keep polling silently.
      }
    }

    load()
    const id = setInterval(load, 30_000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const visible = invalid.filter((s) => !dismissed.has(s))
  if (visible.length === 0) return null

  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
      We removed{' '}
      {visible.map((s, i) => (
        <span key={s}>
          <code className="font-mono">{s}</code>
          {i < visible.length - 1 ? ', ' : ''}
        </span>
      ))}{' '}
      — Greenhouse no longer has boards for{' '}
      {visible.length === 1 ? 'it' : 'them'}.{' '}
      <button
        className="ml-2 underline"
        onClick={() => setDismissed(new Set([...dismissed, ...visible]))}
      >
        Dismiss
      </button>
    </div>
  )
}
