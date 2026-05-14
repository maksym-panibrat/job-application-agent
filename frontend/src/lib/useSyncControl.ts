import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, SyncStatus } from '../api/client'
import { useToast } from '../components/ui/Toast'
import { track } from './track'

const POLL_MS = 3_000
const FAST_SYNC_INVALIDATE_MS = 1_500

export function liveLabel(s: SyncStatus | null): string {
  if (!s) return 'Sync now'
  if (s.state === 'syncing') {
    const done = s.slugs_total - s.slugs_pending
    return `Searching ${done} of ${s.slugs_total} boards…`
  }
  if (s.state === 'matching') {
    return `Scoring ${s.matches_pending} job${s.matches_pending === 1 ? '' : 's'}…`
  }
  return 'Sync now'
}

export interface UseSyncControlOptions {
  /** Skip polling and (in tests) also no-op `trigger`. Default: true. */
  enabled?: boolean
}

export interface SyncControl {
  status: SyncStatus | null
  label: string
  isLive: boolean
  isPending: boolean
  trigger: (source: string) => void
}

export function useSyncControl({ enabled = true }: UseSyncControlOptions = {}): SyncControl {
  const qc = useQueryClient()
  const { show } = useToast()
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [pollKick, setPollKick] = useState(0)
  const prevState = useRef<SyncStatus['state'] | null>(null)
  const fastSyncTimeout = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    let timeoutId: ReturnType<typeof setTimeout> | null = null
    async function poll() {
      try {
        const body = await api.getSyncStatus()
        if (cancelled) return
        setStatus(body)
        if (prevState.current && prevState.current !== 'idle' && body.state === 'idle') {
          qc.invalidateQueries({ queryKey: ['applications'] })
        }
        prevState.current = body.state
        if (body.state !== 'idle') {
          timeoutId = setTimeout(poll, POLL_MS)
        }
      } catch {
        // Silent — control just stays "Sync now" without live state.
      }
    }
    poll()
    return () => {
      cancelled = true
      if (timeoutId) clearTimeout(timeoutId)
    }
  }, [qc, enabled, pollKick])

  // Cleanup any in-flight delayed invalidation on unmount so it can't fire
  // against a stale QueryClient (e.g., after sign-out).
  useEffect(() => () => {
    if (fastSyncTimeout.current) clearTimeout(fastSyncTimeout.current)
  }, [])

  const sync = useMutation({
    mutationFn: api.triggerSync,
    onSuccess: (data) => {
      track('feed.sync_succeeded', {
        matched_now: data.matched_now ?? 0,
        queued_slugs: data.queued_slugs?.length ?? 0,
      })
      show(`Searching now — ${data.matched_now ?? 0} from cache.`, 'success')
      // Belt-and-suspenders for fast syncs that finish before the poller
      // catches a non-idle state — surfaces `matched_now` cache hits in the feed.
      if (fastSyncTimeout.current) clearTimeout(fastSyncTimeout.current)
      setPollKick((value) => value + 1)
      fastSyncTimeout.current = setTimeout(
        () => qc.invalidateQueries({ queryKey: ['applications'] }),
        FAST_SYNC_INVALIDATE_MS,
      )
    },
    onError: (err) => {
      track('feed.sync_failed', { error: (err as Error)?.message ?? 'unknown' })
      show((err as Error)?.message ?? 'Sync failed — try again', 'error')
    },
  })

  return {
    status,
    label: liveLabel(status),
    isLive: !!(status?.state && status.state !== 'idle'),
    isPending: sync.isPending,
    trigger: (source: string) => {
      if (!enabled) return
      track('feed.sync_clicked', { source })
      sync.mutate()
    },
  }
}
