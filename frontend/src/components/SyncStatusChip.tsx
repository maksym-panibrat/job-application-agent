import { useEffect, useRef, useState } from 'react'
import { api, SyncStatus } from '../api/client'

/**
 * Pollable status pill for the dashboard. Hidden when state==='idle'.
 *
 * Polls /api/sync/status every 3s. When the polled state transitions
 * back to 'idle' (i.e. the active sync/match wave finished), invokes
 * the optional `onIdle` callback so the parent can refetch matches.
 */
export function SyncStatusChip({ onIdle }: { onIdle?: () => void }) {
  const [status, setStatus] = useState<SyncStatus | null>(null)
  // Keep the latest onIdle in a ref so the polling effect doesn't tear
  // down + restart every time the parent re-renders with a new closure.
  const onIdleRef = useRef(onIdle)
  useEffect(() => {
    onIdleRef.current = onIdle
  }, [onIdle])

  useEffect(() => {
    let cancelled = false
    let prevState: SyncStatus['state'] | null = null

    async function poll() {
      try {
        const body = await api.getSyncStatus()
        if (cancelled) return
        setStatus(body)
        if (prevState && prevState !== 'idle' && body.state === 'idle') {
          onIdleRef.current?.()
        }
        prevState = body.state
      } catch {
        // Network error — keep polling silently.
      }
    }

    poll()
    const id = setInterval(poll, 3000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  if (!status || status.state === 'idle') return null

  const text =
    status.state === 'syncing'
      ? `Syncing ${status.slugs_pending} of ${status.slugs_total} boards`
      : `Scoring ${status.matches_pending} job${status.matches_pending === 1 ? '' : 's'}`

  return (
    <span className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-sm text-blue-700">
      <span className="h-2 w-2 animate-pulse rounded-full bg-blue-500" />
      {text}
    </span>
  )
}
